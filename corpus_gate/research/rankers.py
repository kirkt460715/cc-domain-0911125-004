import json
import math
from corpus_gate.errors import digest


def load_index(db, index_id, members, expected=None):
    '''Load term statistics for the frozen member set from a fixed search index.

    Every statistic comes from the stored index rows; nothing is recomputed
    from live documents. Any checksum drift is reported so callers can mark
    scores as index_unavailable instead of trusting a corrupt index.
    '''
    empty = {'ok': False, 'problems': [{'code': 'research_index_missing'}], 'documents': {}, 'df': {}, 'average_tokens': 0.0}
    row = db.one('SELECT * FROM search_indexes WHERE index_id=?', (index_id,))
    if row is None:
        return empty
    problems = []
    evidence = [{'document_id': entry['document_id'], 'digest': entry['digest']} for entry in db.all('SELECT document_id,digest FROM search_documents WHERE index_id=? ORDER BY document_id', (index_id,))]
    if len(evidence) != row['documents'] or digest(evidence) != row['digest']:
        problems.append({'code': 'research_index_manifest'})
    if expected is not None and row['digest'] != expected:
        problems.append({'code': 'research_index_changed'})
    documents = {}
    for member in members:
        stored = db.one('SELECT * FROM search_documents WHERE index_id=? AND document_id=?', (index_id, member['document_id']))
        if stored is None:
            problems.append({'code': 'research_index_document', 'document_id': member['document_id']})
            continue
        terms = json.loads(stored['terms'])
        if len(terms) != stored['tokens'] or digest(terms) != stored['digest']:
            problems.append({'code': 'research_index_digest', 'document_id': member['document_id']})
            continue
        counts = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        documents[member['document_id']] = {'counts': counts, 'tokens': stored['tokens']}
    df = {}
    for entry in documents.values():
        for term in entry['counts']:
            df[term] = df.get(term, 0) + 1
    total = sum(entry['tokens'] for entry in documents.values())
    return {'ok': not problems, 'problems': problems, 'documents': documents, 'df': df, 'average_tokens': total / len(documents) if documents else 0.0, 'index_digest': row['digest']}


def score_tf(index, terms):
    '''Replicate the search.query ordering: distinct matched terms, then raw
    term frequency, then shorter document, then identifier.'''
    wanted = set(terms)
    ranked = []
    for document_id, entry in index['documents'].items():
        matched = wanted & set(entry['counts'])
        if not matched:
            continue
        frequency = sum(entry['counts'][term] for term in matched)
        ranked.append({'document_id': document_id, 'detail': {'matched_terms': len(matched), 'term_frequency': frequency, 'tokens': entry['tokens']}, 'key': (-len(matched), -frequency, entry['tokens'], document_id)})
    ranked.sort(key=lambda item: item['key'])
    for item in ranked:
        del item['key']
    return ranked


def score_bm25(index, terms, k1, b):
    '''BM25 with the non-negative idf form ln(1 + (N - df + 0.5) / (df + 0.5));
    ties break on document identifier so input order never decides ranks.'''
    documents = index['documents']
    total = len(documents)
    average = index['average_tokens']
    ranked = []
    for document_id, entry in documents.items():
        score = 0.0
        for term in terms:
            frequency = entry['counts'].get(term)
            if not frequency:
                continue
            idf = math.log(1 + (total - index['df'][term] + 0.5) / (index['df'][term] + 0.5))
            norm = 1 - b + b * entry['tokens'] / average
            score += idf * (frequency * (k1 + 1)) / (frequency + k1 * norm)
        if score > 0:
            ranked.append({'document_id': document_id, 'detail': {'score': score}, 'key': (-score, document_id)})
    ranked.sort(key=lambda item: item['key'])
    for item in ranked:
        del item['key']
    return ranked
