import math
import random
from corpus_gate.errors import integer, digest, fail
from corpus_gate import documents
from corpus_gate.ingest import sources
from corpus_gate.research import metrics, rankers

SYSTEMS = ('tf', 'bm25')

CONVENTIONS = {'gain': '2^grade - 1', 'discount': '1 / log2(rank + 1)', 'unjudged_gain': 0, 'recall_basis': 'judged relevant units only; unjudged units are never counted as relevant', 'dedup': 'reposts sharing exact_hash collapse into one information unit', 'tie_break': 'fixed score keys then document_id; input order never decides ranks', 'weighting': 'query families carry equal weight; rewrites vote inside their family only', 'resample_unit': 'family'}


def universe(db, study):
    members = db.all('SELECT * FROM research_documents WHERE study_id=? AND version=? ORDER BY document_id', (study['study_id'], study['version']))
    index = rankers.load_index(db, study['index_id'], members, expected=study['index_digest'])
    return {'members': members, 'units': {row['document_id']: row['unit'] for row in members}, 'index': index}


def judgments(db, study, family_id):
    return {row['unit']: row['grade'] for row in db.all('SELECT unit,grade FROM research_judgments WHERE study_id=? AND version=? AND family_id=?', (study['study_id'], study['version'], family_id))}


def unavailable():
    return {'status': 'index_unavailable', 'retrieved': 0, 'retrieved_units': 0, 'ranking': [], 'dcg': None, 'idcg': None, 'ndcg': None, 'recall': None, 'coverage': None, 'judged_relevant': 0, 'judged_units': 0}


def cell(ranked, world, graded, k):
    units = []
    seen = set()
    for item in ranked:
        unit = world['units'][item['document_id']]
        if unit in seen:
            continue
        seen.add(unit)
        units.append({'unit': unit, 'document_id': item['document_id'], 'detail': item['detail']})
    result = metrics.evaluate(units, graded, k)
    result['status'] = 'ok' if units else 'no_hits'
    result['retrieved'] = len(ranked)
    return result


def evaluate_family(db, study, world, family, k, parameters):
    graded = judgments(db, study, family['family_id'])
    variants = []
    for variant in family['variants']:
        if not world['index']['ok']:
            systems = {name: unavailable() for name in SYSTEMS}
        else:
            ranked = {'tf': rankers.score_tf(world['index'], variant['terms']), 'bm25': rankers.score_bm25(world['index'], variant['terms'], parameters['k1'], parameters['b'])}
            systems = {name: cell(ranked[name], world, graded, k) for name in SYSTEMS}
        variants.append({'variant_id': variant['variant_id'], 'text': variant['text'], 'terms': variant['terms'], 'systems': systems})
    scores = {name: {'ndcg': metrics.mean([variant['systems'][name]['ndcg'] for variant in variants]), 'recall': metrics.mean([variant['systems'][name]['recall'] for variant in variants])} for name in SYSTEMS}
    return {'family_id': family['family_id'], 'kind': family['kind'], 'variants': variants, 'scores': scores, 'difference': difference(scores)}


def difference(scores):
    return {metric: scores['bm25'][metric] - scores['tf'][metric] if scores['bm25'][metric] is not None and scores['tf'][metric] is not None else None for metric in ('ndcg', 'recall')}


def bootstrap(differences, resamples, seed):
    if len(differences) < 2:
        return None
    rng = random.Random(seed)
    count = len(differences)
    means = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += differences[rng.randrange(count)]
        means.append(total / count)
    means.sort()

    def quantile(probability):
        position = probability * (len(means) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return means[lower]
        return means[lower] + (means[upper] - means[lower]) * (position - lower)

    return {'estimate': sum(differences) / count, 'lower': quantile(0.025), 'upper': quantile(0.975), 'families': count}


def report(db, study, k=10, resamples=1000):
    integer(k, 'k', 1, 100)
    integer(resamples, 'resamples', 100, 100000)
    parameters = study['parameters']
    world = universe(db, study)
    evaluated = [evaluate_family(db, study, world, family, k, parameters) for family in study['families'] if family['role'] == 'eval']
    overall = {name: {'ndcg': metrics.mean([family['scores'][name]['ndcg'] for family in evaluated]), 'recall': metrics.mean([family['scores'][name]['recall'] for family in evaluated])} for name in SYSTEMS}
    seed = int(digest([study['study_id'], study['version'], 'resample'])[:16], 16)
    paired = [family for family in evaluated if family['difference']['ndcg'] is not None]
    paired_recall = [family for family in evaluated if family['difference']['recall'] is not None]
    groups = {}
    for family in evaluated:
        groups.setdefault(family['kind'], []).append(family)
    grouped = []
    for kind, rows in sorted(groups.items()):
        scores = {name: {'ndcg': metrics.mean([family['scores'][name]['ndcg'] for family in rows]), 'recall': metrics.mean([family['scores'][name]['recall'] for family in rows])} for name in SYSTEMS}
        grouped.append({'kind': kind, 'families': len(rows), 'tf': scores['tf'], 'bm25': scores['bm25'], 'difference': difference(scores)})
    differences = [{'family_id': family['family_id'], 'kind': family['kind'], 'ndcg': family['difference']['ndcg'], 'recall': family['difference']['recall']} for family in evaluated]
    differences.sort(key=lambda row: (row['ndcg'] is None, row['ndcg'] if row['ndcg'] is not None else 0.0, row['family_id']))
    return {'study_id': study['study_id'], 'version': study['version'], 'k': k, 'index': {'index_id': study['index_id'], 'digest': study['index_digest'], 'verified': world['index']['ok'], 'problems': world['index']['problems']}, 'systems': {'tf': 'existing term-frequency matching, same ordering as search.query', 'bm25': {'description': 'BM25 candidate; statistics come only from the frozen index', 'k1': parameters['k1'], 'b': parameters['b']}}, 'families': evaluated, 'overall': {'tf': overall['tf'], 'bm25': overall['bm25'], 'difference': difference(overall)}, 'intervals': {'unit': 'family', 'resamples': resamples, 'confidence': 0.95, 'ndcg': bootstrap([family['difference']['ndcg'] for family in paired], resamples, seed), 'recall': bootstrap([family['difference']['recall'] for family in paired_recall], resamples, seed)}, 'groups': grouped, 'family_differences': differences, 'regressions': [row['family_id'] for row in differences if row['ndcg'] is not None and row['ndcg'] < 0], 'conventions': CONVENTIONS}


def export(db, study, k=10, include_snippets=True):
    with db.transaction():
        result = report(db, study, k)
        members = universe(db, study)['members']
        involved = sorted({row['source_id'] for row in members})
        failures = []
        for source_id in involved:
            decision = sources.authorize(db, source_id, study['context'], exporting=True)
            if not decision['allowed']:
                failures.append(decision)
        if include_snippets and failures:
            fail('research_export_authorization', 'Current authorization blocks original text snippets', failures=failures)
        if include_snippets:
            for family in result['families']:
                for variant in family['variants']:
                    for system in variant['systems'].values():
                        for entry in system['ranking']:
                            entry['snippet'] = documents.get(db, entry['document_id'])['normalized'][:160]
        numeric = all(study['authorization'][source_id]['allowed'] for source_id in involved)
        payload = {'format': 'corpus-gate-research', 'version': 1, 'study_id': study['study_id'], 'study_version': study['version'], 'k': k, 'generated_at': db.clock(), 'context': study['context'], 'authorization': {'numeric': {'allowed': numeric, 'basis': 'frozen', 'decisions': study['authorization']}, 'snippets': {'included': include_snippets, 'allowed': not failures, 'basis': 'current', 'failures': failures}}, 'report': result}
        db.log('research.exported', {'study_id': study['study_id'], 'version': study['version'], 'k': k, 'snippets': include_snippets, 'digest': digest(payload)})
        return {'payload': payload, 'sha256': digest(payload)}
