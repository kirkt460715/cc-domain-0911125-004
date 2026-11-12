import json
import math
from corpus_gate.errors import identifier, integer, text, shape, canonical, fail
from corpus_gate import documents
from corpus_gate.ingest import sources
from corpus_gate.review import desk

DDL = '''
CREATE TABLE IF NOT EXISTS research_studies(study_id TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,index_id TEXT NOT NULL,index_digest TEXT NOT NULL,corpus_revision INTEGER NOT NULL,context TEXT NOT NULL,authorization TEXT NOT NULL,parameters TEXT,created_at TEXT NOT NULL,locked_at TEXT,PRIMARY KEY(study_id,version));
CREATE TABLE IF NOT EXISTS research_documents(study_id TEXT NOT NULL,version INTEGER NOT NULL,document_id TEXT NOT NULL,unit TEXT NOT NULL,source_id TEXT NOT NULL,tokens INTEGER NOT NULL,PRIMARY KEY(study_id,version,document_id));
CREATE TABLE IF NOT EXISTS research_families(study_id TEXT NOT NULL,version INTEGER NOT NULL,family_id TEXT NOT NULL,kind TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(study_id,version,family_id));
CREATE TABLE IF NOT EXISTS research_variants(study_id TEXT NOT NULL,version INTEGER NOT NULL,family_id TEXT NOT NULL,variant_id TEXT NOT NULL,text TEXT NOT NULL,terms TEXT NOT NULL,PRIMARY KEY(study_id,version,family_id,variant_id));
CREATE TABLE IF NOT EXISTS research_judgments(study_id TEXT NOT NULL,version INTEGER NOT NULL,family_id TEXT NOT NULL,unit TEXT NOT NULL,document_id TEXT NOT NULL,grade INTEGER NOT NULL,actor TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(study_id,version,family_id,unit));
'''

ROLES = {'dev', 'eval'}


def number(value, field, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        fail('research_number', 'Expected a bounded finite number', field=field)
    return float(value)


def query_terms(value):
    from corpus_gate.text.normalize import tokens
    terms = sorted({token['text'].casefold() for token in tokens(value)})
    if not terms or len(terms) > 30:
        fail('research_query_terms', 'Query text must yield one to thirty distinct terms')
    return terms


class Studies:
    def __init__(self, db):
        self.db = db
        if db.depth:
            fail('research_transaction', 'Initialize research store outside transactions')
        db.db.executescript(DDL)

    def create(self, study_id, index_id, context):
        identifier(study_id)
        identifier(index_id, 'index_id')
        context = sources.request(context)
        from corpus_gate.text.search import SearchIndex
        checked = SearchIndex(self.db).verify(index_id)
        if not checked['ok']:
            fail('research_index', 'Search index must verify before a study freezes it', problems=checked['problems'])
        with self.db.transaction():
            if self.db.one('SELECT study_id FROM research_studies WHERE study_id=?', (study_id,)):
                fail('research_exists', 'Research study identifiers are immutable')
            index = self.db.one('SELECT * FROM search_indexes WHERE index_id=?', (index_id,))
            snapshot = {}
            members = []
            for row in self.db.all('SELECT document_id,tokens FROM search_documents WHERE index_id=? ORDER BY document_id', (index_id,)):
                document = documents.get(self.db, row['document_id'])
                decision = sources.authorize(self.db, document['source_id'], context)
                snapshot[document['source_id']] = decision
                if decision['allowed']:
                    members.append({'document_id': row['document_id'], 'unit': document['exact_hash'], 'source_id': document['source_id'], 'tokens': row['tokens']})
            if not members:
                fail('research_empty', 'No indexed documents are visible under this authorization')
            self.db.execute("INSERT INTO research_studies VALUES(?,1,'open',?,?,?,?,?,NULL,?,NULL)", (study_id, index_id, index['digest'], self.db.revision(), canonical(context), canonical(snapshot), self.db.clock()))
            for member in members:
                self.db.execute('INSERT INTO research_documents VALUES(?,?,?,?,?,?)', (study_id, 1, member['document_id'], member['unit'], member['source_id'], member['tokens']))
            self.db.log('research.created', {'study_id': study_id, 'index_id': index_id, 'documents': len(members)})
            return self.get(study_id)

    def get(self, study_id, version=None):
        identifier(study_id)
        if version is None:
            row = self.db.one('SELECT * FROM research_studies WHERE study_id=? ORDER BY version DESC LIMIT 1', (study_id,))
        else:
            integer(version, 'version', 1)
            row = self.db.one('SELECT * FROM research_studies WHERE study_id=? AND version=?', (study_id, version))
        if row is None:
            fail('research_missing', 'Research study does not exist')
        row['context'] = json.loads(row['context'])
        row['authorization'] = json.loads(row['authorization'])
        row['parameters'] = json.loads(row['parameters']) if row['parameters'] else None
        row['families'] = self.families(study_id, row['version'])
        row['documents'] = self.db.scalar('SELECT COUNT(*) FROM research_documents WHERE study_id=? AND version=?', (study_id, row['version']))
        return row

    def families(self, study_id, version):
        rows = self.db.all('SELECT * FROM research_families WHERE study_id=? AND version=? ORDER BY family_id', (study_id, version))
        for row in rows:
            row['variants'] = self.db.all('SELECT variant_id,text,terms FROM research_variants WHERE study_id=? AND version=? AND family_id=? ORDER BY variant_id', (study_id, version, row['family_id']))
            for variant in row['variants']:
                variant['terms'] = json.loads(variant['terms'])
            row['judgments'] = self.db.scalar('SELECT COUNT(*) FROM research_judgments WHERE study_id=? AND version=? AND family_id=?', (study_id, version, row['family_id']))
        return rows

    def listing(self):
        return [self.get(row['study_id'], row['version']) for row in self.db.all('SELECT study_id,version FROM research_studies ORDER BY study_id,version')]

    def editable(self, study_id):
        row = self.db.one('SELECT * FROM research_studies WHERE study_id=? ORDER BY version DESC LIMIT 1', (study_id,))
        if row is None:
            fail('research_missing', 'Research study does not exist')
        if row['state'] != 'open':
            fail('research_locked', 'Locked comparison sets are immutable; revise to a new version')
        return row

    def locked(self, study_id, version=None):
        identifier(study_id)
        if version is None:
            row = self.db.one("SELECT * FROM research_studies WHERE study_id=? AND state='locked' ORDER BY version DESC LIMIT 1", (study_id,))
            if row is None:
                if not self.db.one('SELECT study_id FROM research_studies WHERE study_id=?', (study_id,)):
                    fail('research_missing', 'Research study does not exist')
                fail('research_unlocked', 'Lock the comparison set before reporting')
        else:
            integer(version, 'version', 1)
            row = self.db.one('SELECT * FROM research_studies WHERE study_id=? AND version=?', (study_id, version))
            if row is None:
                fail('research_missing', 'Research study version does not exist')
            if row['state'] != 'locked':
                fail('research_unlocked', 'Reports are computed on locked versions only')
        return self.get(study_id, row['version'])

    def insert_variant(self, study_id, version, family_id, variant):
        shape(variant, {'variant_id', 'text'})
        identifier(variant['variant_id'], 'variant_id')
        body = text(variant['text'], 'text', 1000)
        terms = query_terms(body)
        if self.db.one('SELECT variant_id FROM research_variants WHERE study_id=? AND version=? AND family_id=? AND variant_id=?', (study_id, version, family_id, variant['variant_id'])):
            fail('research_variant_exists', 'Query variant identifier repeats in this family')
        self.db.execute('INSERT INTO research_variants VALUES(?,?,?,?,?,?)', (study_id, version, family_id, variant['variant_id'], body, canonical(terms)))

    def add_family(self, study_id, family_id, kind, role, variants):
        identifier(family_id, 'family_id')
        identifier(kind, 'kind')
        if role not in ROLES:
            fail('research_role', 'Query family role must be dev or eval')
        if not isinstance(variants, list) or not 1 <= len(variants) <= 30:
            fail('research_variants', 'A query family needs one to thirty rewrites')
        with self.db.transaction():
            study = self.editable(study_id)
            if self.db.one('SELECT family_id FROM research_families WHERE study_id=? AND version=? AND family_id=?', (study_id, study['version'], family_id)):
                fail('research_family_exists', 'Query family identifier repeats in this version')
            self.db.execute('INSERT INTO research_families VALUES(?,?,?,?,?,?)', (study_id, study['version'], family_id, kind, role, self.db.clock()))
            for variant in variants:
                self.insert_variant(study_id, study['version'], family_id, variant)
            self.db.log('research.family_added', {'study_id': study_id, 'version': study['version'], 'family_id': family_id, 'kind': kind, 'role': role, 'variants': len(variants)})
            return self.get(study_id)

    def add_variant(self, study_id, family_id, variant_id, text):
        with self.db.transaction():
            study = self.editable(study_id)
            if not self.db.one('SELECT family_id FROM research_families WHERE study_id=? AND version=? AND family_id=?', (study_id, study['version'], family_id)):
                fail('research_family_missing', 'Query family does not exist in this version')
            if self.db.scalar('SELECT COUNT(*) FROM research_variants WHERE study_id=? AND version=? AND family_id=?', (study_id, study['version'], family_id)) >= 30:
                fail('research_variants', 'A query family holds at most thirty rewrites')
            self.insert_variant(study_id, study['version'], family_id, {'variant_id': variant_id, 'text': text})
            self.db.log('research.variant_added', {'study_id': study_id, 'version': study['version'], 'family_id': family_id, 'variant_id': variant_id})
            return self.get(study_id)

    def judge(self, study_id, family_id, document_id, grade, actor):
        identifier(family_id, 'family_id')
        integer(grade, 'grade', 0, 3)
        desk.actor(self.db, actor)
        with self.db.transaction():
            study = self.editable(study_id)
            if not self.db.one('SELECT family_id FROM research_families WHERE study_id=? AND version=? AND family_id=?', (study_id, study['version'], family_id)):
                fail('research_family_missing', 'Query family does not exist in this version')
            member = self.db.one('SELECT * FROM research_documents WHERE study_id=? AND version=? AND document_id=?', (study_id, study['version'], document_id))
            if member is None:
                fail('research_document', 'Document is outside the frozen study set')
            existing = self.db.one('SELECT * FROM research_judgments WHERE study_id=? AND version=? AND family_id=? AND unit=?', (study_id, study['version'], family_id, member['unit']))
            if existing and existing['grade'] == grade:
                return existing
            if existing:
                self.db.execute('UPDATE research_judgments SET grade=?,document_id=?,actor=?,revision=revision+1,updated_at=? WHERE study_id=? AND version=? AND family_id=? AND unit=?', (grade, document_id, actor, self.db.clock(), study_id, study['version'], family_id, member['unit']))
                action = 'research.rejudged'
            else:
                self.db.execute('INSERT INTO research_judgments VALUES(?,?,?,?,?,?,?,1,?)', (study_id, study['version'], family_id, member['unit'], document_id, grade, actor, self.db.clock()))
                action = 'research.judged'
            self.db.log(action, {'study_id': study_id, 'version': study['version'], 'family_id': family_id, 'unit': member['unit'], 'grade': grade, 'actor': actor})
            return self.db.one('SELECT * FROM research_judgments WHERE study_id=? AND version=? AND family_id=? AND unit=?', (study_id, study['version'], family_id, member['unit']))

    def tune(self, study_id, k1_values=None, b_values=None, k=10):
        integer(k, 'k', 1, 100)
        k1_values = [1.2, 1.5, 2.0] if k1_values is None else k1_values
        b_values = [0.25, 0.5, 0.75] if b_values is None else b_values
        if not isinstance(k1_values, list) or not 1 <= len(k1_values) <= 8 or not isinstance(b_values, list) or not 1 <= len(b_values) <= 8:
            fail('research_grid', 'Parameter grids need one to eight values each')
        k1s = sorted({number(value, 'k1', 0.0, 3.0) for value in k1_values})
        bs = sorted({number(value, 'b', 0.0, 1.0) for value in b_values})
        with self.db.transaction():
            study = self.get(study_id, self.editable(study_id)['version'])
            dev = [family for family in study['families'] if family['role'] == 'dev']
            if not dev:
                fail('research_no_dev', 'Parameter selection needs at least one dev query family')
            from corpus_gate.research import report as reporting
            world = reporting.universe(self.db, study)
            if not world['index']['ok']:
                fail('research_index', 'Frozen index no longer verifies', problems=world['index']['problems'])
            candidates = []
            for k1 in k1s:
                for b in bs:
                    parameters = {'k1': k1, 'b': b}
                    scores = [reporting.evaluate_family(self.db, study, world, family, k, parameters)['scores']['bm25']['ndcg'] for family in dev]
                    computable = [value for value in scores if value is not None]
                    candidates.append({'k1': k1, 'b': b, 'dev_ndcg': sum(computable) / len(computable) if computable else None, 'computable_families': len(computable)})
            if not any(row['dev_ndcg'] is not None for row in candidates):
                fail('research_tuning_empty', 'No dev family yields a computable NDCG; add dev judgments')
            best = min((row for row in candidates if row['dev_ndcg'] is not None), key=lambda row: (-round(row['dev_ndcg'], 9), row['k1'], row['b']))
            parameters = {'k1': best['k1'], 'b': best['b'], 'k': k, 'dev_families': [family['family_id'] for family in dev], 'dev_ndcg': best['dev_ndcg'], 'candidates': candidates, 'tuned_at': self.db.clock()}
            self.db.execute('UPDATE research_studies SET parameters=? WHERE study_id=? AND version=?', (canonical(parameters), study_id, study['version']))
            self.db.log('research.tuned', {'study_id': study_id, 'version': study['version'], 'k1': best['k1'], 'b': best['b'], 'dev_ndcg': best['dev_ndcg']})
            return self.get(study_id)

    def lock(self, study_id):
        with self.db.transaction():
            study = self.get(study_id, self.editable(study_id)['version'])
            if study['parameters'] is None:
                fail('research_untuned', 'Select candidate parameters on dev families before locking')
            evaluated = [family for family in study['families'] if family['role'] == 'eval']
            if not evaluated:
                fail('research_no_eval', 'Locking needs at least one eval query family')
            for family in evaluated:
                if family['judgments'] < 1:
                    fail('research_unjudged', 'Every eval family needs at least one judgment', family_id=family['family_id'])
            self.db.execute("UPDATE research_studies SET state='locked',locked_at=? WHERE study_id=? AND version=?", (self.db.clock(), study_id, study['version']))
            self.db.log('research.locked', {'study_id': study_id, 'version': study['version'], 'eval_families': len(evaluated)})
            return self.get(study_id)

    def revise(self, study_id):
        with self.db.transaction():
            latest = self.db.one('SELECT * FROM research_studies WHERE study_id=? ORDER BY version DESC LIMIT 1', (study_id,))
            if latest is None:
                fail('research_missing', 'Research study does not exist')
            if latest['state'] == 'open':
                fail('research_open', 'Lock the open version before revising')
            version = latest['version'] + 1
            self.db.execute("INSERT INTO research_studies VALUES(?,?,'open',?,?,?,?,?,?,?,NULL)", (study_id, version, latest['index_id'], latest['index_digest'], latest['corpus_revision'], latest['context'], latest['authorization'], latest['parameters'], self.db.clock()))
            self.db.execute('INSERT INTO research_documents SELECT study_id,?,document_id,unit,source_id,tokens FROM research_documents WHERE study_id=? AND version=?', (version, study_id, latest['version']))
            self.db.execute('INSERT INTO research_families SELECT study_id,?,family_id,kind,role,created_at FROM research_families WHERE study_id=? AND version=?', (version, study_id, latest['version']))
            self.db.execute('INSERT INTO research_variants SELECT study_id,?,family_id,variant_id,text,terms FROM research_variants WHERE study_id=? AND version=?', (version, study_id, latest['version']))
            self.db.execute('INSERT INTO research_judgments SELECT study_id,?,family_id,unit,document_id,grade,actor,revision,updated_at FROM research_judgments WHERE study_id=? AND version=?', (version, study_id, latest['version']))
            self.db.log('research.revised', {'study_id': study_id, 'version': version, 'from_version': latest['version']})
            return self.get(study_id)

    def report(self, study_id, version=None, k=10, resamples=1000):
        study = self.locked(study_id, version)
        from corpus_gate.research import report as reporting
        return reporting.report(self.db, study, k, resamples)

    def export(self, study_id, version=None, k=10, include_snippets=True):
        study = self.locked(study_id, version)
        if type(include_snippets) is not bool:
            fail('research_flag', 'include_snippets must be boolean')
        from corpus_gate.research import report as reporting
        return reporting.export(self.db, study, k, include_snippets)
