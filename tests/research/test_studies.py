import math
from tests.support import Case, record, context
from corpus_gate.ingest import sources
from corpus_gate.review import desk

FILLERS = ' '.join(f'f{index:03d}' for index in range(1, 199)).replace('f100', 'delta')
RECORDS = [
    ('alpha-overview', 'alpha beta gamma delta epsilon'),
    ('alpha-padded', 'alpha alpha ' + FILLERS),
    ('quasar-brief', 'quasar delta briefing notes overview'),
    ('quasar-blast', 'quasar ' * 16 + 'blast memo appendix figures'),
    ('gamma-report', 'gamma gamma gamma report summary delta'),
    ('delta-memo', 'delta memo appendix figures delta delta'),
    ('quasar-repost', 'quasar delta briefing notes overview'),
]
JUDGMENTS = [
    ('dev-gamma', 'gamma-report', 2),
    ('dev-gamma', 'alpha-overview', 1),
    ('eval-hf', 'alpha-overview', 2),
    ('eval-hf', 'alpha-padded', 0),
    ('eval-pro', 'quasar-brief', 2),
    ('eval-pro', 'quasar-blast', 0),
    ('eval-pro', 'delta-memo', 0),
    ('eval-pro', 'alpha-overview', 0),
    ('eval-pro', 'gamma-report', 0),
    ('eval-pro', 'alpha-padded', 0),
]


def bm25(frequency, df, length, average, k1=1.2, b=0.25, total=7):
    idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
    return idf * (frequency * (k1 + 1)) / (frequency + k1 * (1 - b + b * length / average))


class StudyTests(Case):
    def build_study(self, judgments=JUDGMENTS):
        self.ingest([record(identity, value) for identity, value in RECORDS])
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.app.research.add_family('study', 'dev-gamma', 'development', 'dev', [{'variant_id': 'v1', 'text': 'gamma'}])
        self.app.research.add_family('study', 'eval-hf', 'high_frequency', 'eval', [{'variant_id': 'v1', 'text': 'alpha'}, {'variant_id': 'v2', 'text': 'Alpha'}])
        self.app.research.add_family('study', 'eval-pro', 'professional', 'eval', [{'variant_id': 'v1', 'text': 'quasar delta'}, {'variant_id': 'v2', 'text': 'delta quasar'}])
        for family_id, external, grade in judgments:
            self.app.research.judge('study', family_id, self.document(external)['document_id'], grade, 'editor')
        self.app.research.tune('study')
        self.app.research.lock('study')

    def families(self, result):
        return {row['family_id']: row for row in result['families']}

    def test_candidate_wins_high_frequency_loses_professional(self):
        self.build_study()
        study = self.app.research.get('study')
        self.assertEqual(study['parameters']['k1'], 1.2)
        self.assertEqual(study['parameters']['b'], 0.25)
        self.assertEqual(study['parameters']['dev_families'], ['dev-gamma'])
        result = self.app.research.report('study', k=2)
        families = self.families(result)
        pro = families['eval-pro']['variants'][0]['systems']
        hf = families['eval-hf']['variants'][0]['systems']
        # 手算：增益 2^2-1=3；名次 1 折损 1/log2(2)=1，名次 2 折损 1/log2(3)
        self.assertEqual([entry['gain'] for entry in pro['tf']['ranking']], [3, 0])
        self.assertEqual(pro['tf']['ranking'][0]['discount'], 1.0)
        self.assertAlmostEqual(pro['tf']['ranking'][1]['discount'], 1.0 / math.log2(3))
        self.assertEqual(pro['tf']['dcg'], 3.0)
        self.assertEqual(pro['tf']['idcg'], 3.0)
        self.assertEqual(pro['tf']['ndcg'], 1.0)
        self.assertAlmostEqual(pro['bm25']['dcg'], 3.0 / math.log2(3))
        self.assertAlmostEqual(pro['bm25']['ndcg'], 1.0 / math.log2(3))
        self.assertAlmostEqual(hf['tf']['ndcg'], 1.0 / math.log2(3))
        self.assertEqual(hf['bm25']['ndcg'], 1.0)
        for system in (pro['tf'], pro['bm25'], hf['tf'], hf['bm25']):
            self.assertEqual(system['recall'], 1.0)
            self.assertEqual(system['coverage'], 1.0)
        # 手算 BM25 得分：N=7，avgdl=247/7，df(quasar)=3，df(delta)=6，df(alpha)=2
        average = 247 / 7
        blast = self.document('quasar-blast')['document_id']
        brief_unit = self.document('quasar-brief')['exact_hash']
        entries = {entry['unit']: entry for entry in pro['bm25']['ranking']}
        self.assertAlmostEqual(entries[self.document('quasar-blast')['exact_hash']]['detail']['score'], bm25(16, 3, 20, average))
        self.assertAlmostEqual(entries[brief_unit]['detail']['score'], bm25(1, 3, 5, average) + bm25(1, 6, 5, average))
        self.assertGreater(entries[self.document('quasar-blast')['exact_hash']]['detail']['score'], entries[brief_unit]['detail']['score'])
        hf_entries = {entry['unit']: entry for entry in hf['bm25']['ranking']}
        self.assertAlmostEqual(hf_entries[self.document('alpha-overview')['exact_hash']]['detail']['score'], bm25(1, 2, 5, average))
        self.assertAlmostEqual(hf_entries[self.document('alpha-padded')['exact_hash']]['detail']['score'], bm25(2, 2, 200, average))
        self.assertLess(hf_entries[self.document('alpha-padded')['exact_hash']]['detail']['score'], hf_entries[self.document('alpha-overview')['exact_hash']]['detail']['score'])
        # 同文转载去重：7 篇文档 6 个信息单元
        self.assertEqual(pro['tf']['retrieved'], 7)
        self.assertEqual(pro['tf']['retrieved_units'], 6)
        self.assertEqual(pro['tf']['ranking'][0]['unit'], brief_unit)
        # 按查询族等权的总体差异为零，分组才看得出候选一升一降
        self.assertAlmostEqual(result['overall']['tf']['ndcg'], (1.0 + 1.0 / math.log2(3)) / 2)
        self.assertAlmostEqual(result['overall']['difference']['ndcg'], 0.0)
        groups = {row['kind']: row for row in result['groups']}
        self.assertAlmostEqual(groups['professional']['difference']['ndcg'], 1.0 / math.log2(3) - 1.0)
        self.assertAlmostEqual(groups['high_frequency']['difference']['ndcg'], 1.0 - 1.0 / math.log2(3))
        self.assertEqual(result['regressions'], ['eval-pro'])
        self.assertEqual(result['family_differences'][0]['family_id'], 'eval-pro')
        self.assertEqual(result['intervals']['ndcg']['families'], 2)
        self.assertAlmostEqual(result['intervals']['ndcg']['estimate'], 0.0)
        self.assertLessEqual(result['intervals']['ndcg']['lower'], 0.0)
        self.assertGreaterEqual(result['intervals']['ndcg']['upper'], 0.0)

    def test_baseline_ranker_matches_search_query_order(self):
        self.ingest([record(identity, value) for identity, value in RECORDS])
        self.app.search.build('idx')
        official = self.app.search.query('idx', {'any': ['quasar', 'delta']}, context())
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        from corpus_gate.research import report as reporting, rankers
        world = reporting.universe(self.db, self.app.research.get('study'))
        ranked = rankers.score_tf(world['index'], ['quasar', 'delta'])
        self.assertEqual([row['document_id'] for row in official['results']], [row['document_id'] for row in ranked])

    def test_locked_set_is_immutable_and_revision_carries_on(self):
        self.build_study()
        self.fails('research_locked', self.app.research.judge, 'study', 'eval-pro', self.document('delta-memo')['document_id'], 1, 'editor')
        self.fails('research_locked', self.app.research.tune, 'study')
        self.fails('research_locked', self.app.research.add_family, 'study', 'late', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'late'}])
        revised = self.app.research.revise('study')
        self.assertEqual(revised['version'], 2)
        self.assertEqual(revised['state'], 'open')
        pro = next(row for row in revised['families'] if row['family_id'] == 'eval-pro')
        self.assertEqual(pro['judgments'], 6)
        self.fails('research_open', self.app.research.revise, 'study')
        self.app.research.judge('study', 'eval-pro', self.document('delta-memo')['document_id'], 1, 'editor')
        locked = self.app.research.lock('study')
        self.assertEqual(locked['state'], 'locked')
        first = self.app.research.report('study', version=1, k=2)
        second = self.app.research.report('study', k=2)
        self.assertEqual(second['version'], 2)
        pro_first = self.families(first)['eval-pro']['variants'][0]['systems']['tf']
        pro_second = self.families(second)['eval-pro']['variants'][0]['systems']['tf']
        self.assertEqual(pro_first['ndcg'], 1.0)
        self.assertAlmostEqual(pro_second['idcg'], 3.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(pro_second['ndcg'], 3.0 / (3.0 + 1.0 / math.log2(3)))
        self.assertEqual(pro_second['recall'], 0.5)

    def test_unjudged_and_zero_ideal_gain_stay_uncomputable(self):
        self.ingest([record('a', 'alpha beta gamma delta epsilon'), record('b', 'beta gamma delta epsilon zeta'), record('c', 'unrelated words create separate content here')])
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.app.research.judge('study', 'dev', self.document('a')['document_id'], 2, 'editor')
        self.app.research.add_family('study', 'eval-zero', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.app.research.judge('study', 'eval-zero', self.document('a')['document_id'], 0, 'editor')
        self.app.research.add_family('study', 'eval-partial', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'beta'}])
        self.app.research.judge('study', 'eval-partial', self.document('a')['document_id'], 2, 'editor')
        self.app.research.tune('study')
        self.app.research.lock('study')
        result = self.app.research.report('study', k=2)
        families = self.families(result)
        zero = families['eval-zero']['variants'][0]['systems']['tf']
        self.assertIsNone(zero['ndcg'])
        self.assertIsNone(zero['recall'])
        self.assertEqual(zero['coverage'], 1.0)
        partial = families['eval-partial']['variants'][0]['systems']['tf']
        self.assertEqual(partial['coverage'], 0.5)
        self.assertEqual(partial['recall'], 1.0)
        self.assertIsNotNone(partial['ndcg'])
        self.assertIsNone(result['intervals']['ndcg'])
        self.assertEqual(len(result['families']), 2)

    def test_no_hits_and_index_failure_are_distinct(self):
        self.ingest([record('a', 'alpha beta gamma delta epsilon'), record('b', 'beta gamma delta epsilon zeta')])
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.app.research.judge('study', 'dev', self.document('a')['document_id'], 2, 'editor')
        self.app.research.add_family('study', 'eval-miss', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'zzz'}])
        self.app.research.judge('study', 'eval-miss', self.document('a')['document_id'], 2, 'editor')
        self.app.research.tune('study')
        self.app.research.lock('study')
        result = self.app.research.report('study', k=2)
        cell = self.families(result)['eval-miss']['variants'][0]['systems']['tf']
        self.assertEqual(cell['status'], 'no_hits')
        self.assertEqual(cell['retrieved'], 0)
        self.assertEqual(cell['ndcg'], 0.0)
        self.assertEqual(cell['recall'], 0.0)
        self.assertIsNone(cell['coverage'])
        self.assertTrue(result['index']['verified'])
        self.db.execute("UPDATE search_documents SET terms='[\"tampered\"]' WHERE document_id=?", (self.document('a')['document_id'],))
        result = self.app.research.report('study', k=2)
        self.assertFalse(result['index']['verified'])
        for family in result['families']:
            for variant in family['variants']:
                for system in variant['systems'].values():
                    self.assertEqual(system['status'], 'index_unavailable')
                    self.assertIsNone(system['ndcg'])
                    self.assertIsNone(system['recall'])

    def test_export_follows_source_authorization(self):
        self.build_study()
        envelope = self.app.research.export('study', k=2)
        payload = envelope['payload']
        self.assertTrue(payload['authorization']['snippets']['allowed'])
        self.assertTrue(payload['authorization']['numeric']['allowed'])
        first = payload['report']['families'][0]['variants'][0]['systems']['tf']['ranking'][0]
        self.assertIn('snippet', first)
        sources.revoke(self.db, 'source', 1, 'Withdraw source')
        self.fails('research_export_authorization', self.app.research.export, 'study')
        numeric = self.app.research.export('study', k=2, include_snippets=False)
        self.assertTrue(numeric['payload']['authorization']['numeric']['allowed'])
        self.assertFalse(numeric['payload']['authorization']['snippets']['allowed'])
        for family in numeric['payload']['report']['families']:
            for variant in family['variants']:
                for system in variant['systems'].values():
                    for entry in system['ranking']:
                        self.assertNotIn('snippet', entry)

    def test_rewrites_share_one_family_vote(self):
        self.ingest([record('a', 'alpha beta gamma delta epsilon'), record('b', 'zeta eta theta iota kappa')])
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.app.research.judge('study', 'dev', self.document('a')['document_id'], 2, 'editor')
        self.app.research.add_family('study', 'eval-a', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'alpha'}, {'variant_id': 'v2', 'text': 'Alpha'}, {'variant_id': 'v3', 'text': 'ALPHA'}])
        self.app.research.judge('study', 'eval-a', self.document('a')['document_id'], 2, 'editor')
        self.app.research.add_family('study', 'eval-b', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'zeta'}])
        self.app.research.judge('study', 'eval-b', self.document('a')['document_id'], 2, 'editor')
        self.app.research.tune('study')
        self.app.research.lock('study')
        result = self.app.research.report('study', k=2)
        families = self.families(result)
        self.assertEqual(len(families['eval-a']['variants']), 3)
        self.assertEqual(families['eval-a']['scores']['tf']['ndcg'], 1.0)
        self.assertEqual(families['eval-b']['scores']['tf']['ndcg'], 0.0)
        # 三个同义改写只算一票：(1.0 + 0.0) / 2，而不是 (1+1+1+0) / 4
        self.assertEqual(result['overall']['tf']['ndcg'], 0.5)
        self.assertEqual(result['intervals']['ndcg']['families'], 2)

    def test_tuning_and_locking_discipline(self):
        self.ingest()
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.fails('research_untuned', self.app.research.lock, 'study')
        self.fails('research_no_dev', self.app.research.tune, 'study')
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.fails('research_tuning_empty', self.app.research.tune, 'study')
        self.fails('research_number', self.app.research.tune, 'study', [float('nan')])
        self.fails('research_grid', self.app.research.tune, 'study', [])
        self.app.research.judge('study', 'dev', self.document()['document_id'], 2, 'editor')
        tuned = self.app.research.tune('study')
        self.assertIsNotNone(tuned['parameters'])
        self.fails('research_no_eval', self.app.research.lock, 'study')
        self.app.research.add_family('study', 'eval', 'misc', 'eval', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.fails('research_unjudged', self.app.research.lock, 'study')
        self.app.research.judge('study', 'eval', self.document()['document_id'], 1, 'editor')
        self.assertEqual(self.app.research.lock('study')['state'], 'locked')
        self.fails('research_missing', self.app.research.report, 'other')

    def test_judgment_correction_and_repost_unit(self):
        self.ingest([record('a', 'alpha beta gamma delta epsilon'), record('b', 'alpha beta gamma delta epsilon')])
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        self.app.research.create('study', 'idx', context())
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        first = self.app.research.judge('study', 'dev', self.document('a')['document_id'], 1, 'editor')
        self.assertEqual(first['revision'], 1)
        same = self.app.research.judge('study', 'dev', self.document('b')['document_id'], 1, 'editor')
        self.assertEqual(same['revision'], 1)
        self.assertEqual(first['unit'], same['unit'])
        corrected = self.app.research.judge('study', 'dev', self.document('b')['document_id'], 3, 'editor')
        self.assertEqual(corrected['revision'], 2)
        self.assertEqual(corrected['grade'], 3)
        self.fails('actor_unavailable', self.app.research.judge, 'study', 'dev', self.document('a')['document_id'], 2, 'ghost')
        self.ingest([record('late', 'omega psi phi chi rho')], 'later')
        self.fails('research_document', self.app.research.judge, 'study', 'dev', self.document('late')['document_id'], 2, 'editor')

    def test_creation_requires_verified_index_and_visible_documents(self):
        self.ingest()
        self.app.search.build('idx')
        self.fails('research_empty', self.app.research.create, 'study', 'idx', context(purpose='other'))
        self.app.research.create('study', 'idx', context())
        self.fails('research_unlocked', self.app.research.report, 'study')
        self.fails('research_exists', self.app.research.create, 'study', 'idx', context())
        self.db.execute("DELETE FROM search_postings WHERE term='alpha'")
        self.fails('research_index', self.app.research.create, 'another', 'idx', context())
        studies = self.app.research.listing()
        self.assertEqual(len(studies), 1)
        self.assertEqual(studies[0]['documents'], 1)
        self.assertEqual(self.app.research.get('study')['state'], 'open')

    def test_frozen_authorization_excludes_unauthorized_sources(self):
        from tests.support import authorization
        sources.create(self.db, 'closed', authorization(purposes=['other']))
        self.ingest([record('open-doc', 'alpha beta gamma delta epsilon')])
        self.ingest([record('shut-doc', 'alpha zeta eta theta iota')], 'later', 'closed')
        self.app.search.build('idx')
        desk.register(self.db, 'editor', 'reviewer')
        study = self.app.research.create('study', 'idx', context())
        self.assertEqual(study['documents'], 1)
        self.assertEqual(study['authorization']['closed']['allowed'], False)
        self.assertEqual(study['authorization']['source']['allowed'], True)
        self.app.research.add_family('study', 'dev', 'development', 'dev', [{'variant_id': 'v1', 'text': 'alpha'}])
        self.fails('research_document', self.app.research.judge, 'study', 'dev', self.document('shut-doc', 'closed')['document_id'], 2, 'editor')
