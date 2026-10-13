import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rdflib import Graph, Literal, RDF, XSD
from engine import Engine, CITY, DSS, HERE
from server import make_server


class DSSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.engine=Engine()

    def run_case(self,level='붐빔',source='demo',r1=True,r2=True):
        return self.engine.evaluate({'source':source,'level':level,'rules':{'R1':r1,'R2':r2}})

    def test_crowded_builds_real_rdf_recommendation_with_provenance(self):
        run=self.run_case()
        self.assertEqual(run['counts'],{'facts':7,'inferred':8,'rows':1,'recommendations':1})
        self.assertEqual([s['count'] for s in run['trace']],[2,6])
        graph=Graph().parse(data=run['ttl'],format='turtle')
        rec=next(graph.subjects(RDF.type,DSS.Recommendation))
        self.assertEqual(graph.value(rec,DSS.basedOn),DSS.DemoPopulation_POI014)
        self.assertEqual(graph.value(rec,DSS.action),DSS.AvoidVisit)
        self.assertEqual(graph.value(rec,DSS.derivedBy),DSS.R2)
        self.assertEqual(str(graph.value(rec,DSS.observedAt)),'2025-11-21T01:10:00')
        self.assertIn('강남역 관측이 ‘붐빔’',run['decision']['explanation'])

    def test_other_levels_do_not_imply_a_safe_visit(self):
        for level in ['여유','보통','약간 붐빔']:
            with self.subTest(level=level):
                run=self.run_case(level)
                self.assertFalse(run['decision']['recommended'])
                self.assertEqual(run['counts']['inferred'],0)
                self.assertIn('안전하다는 판단으로 해석하지 않습니다',run['decision']['explanation'])

    def test_missing_observation_is_not_relaxed(self):
        run=self.run_case('미기록')
        self.assertNotIn('level',run['evidence'][0])
        self.assertFalse(run['decision']['recommended'])
        self.assertIn('관측값이 없어',run['decision']['reason'])

    def test_disabled_rules_break_the_chain(self):
        for r1,r2,expected in [(False,True,0),(True,False,2),(False,False,0)]:
            run=self.run_case(r1=r1,r2=r2)
            self.assertFalse(run['decision']['recommended'])
            self.assertEqual(run['counts']['inferred'],expected)
        self.assertTrue(self.run_case()['decision']['recommended'])

    def test_original_is_relaxed_and_forecast_is_not_an_observation(self):
        self.assertIn(Literal('붐빔'),set(self.engine.source.objects(None,CITY.fcst_congest_lvl)))
        run=self.run_case(source='original')
        self.assertEqual(run['evidence'][0]['level'],'여유')
        self.assertFalse(run['decision']['recommended'])
        self.assertIn('PopulationStatus_POI014',run['evidence'][0]['observation'])

    def test_demo_does_not_mutate_original_or_leak_inference(self):
        before=set(self.engine.source)
        self.run_case()
        relaxed=self.run_case('여유')
        self.assertEqual(relaxed['inferred'],[])
        self.assertEqual(before,set(self.engine.source))
        self.assertEqual(str(self.engine.demo.value(DSS.DemoPopulation_POI014,CITY.congestionLevel)),'붐빔')

    def test_rule_file_query_controls_the_outcome(self):
        original=self.engine.queries['R1']
        try:
            self.engine.queries['R1']=original.replace('"붐빔"','"보통"')
            self.assertFalse(self.run_case()['decision']['recommended'])
            self.assertTrue(self.run_case('보통')['decision']['recommended'])
        finally: self.engine.queries['R1']=original

    def test_latest_observation_only(self):
        obs=DSS.LaterObservation
        triples=[(CITY.Area_POI014,CITY.hasPopulationStatus,obs),(obs,RDF.type,CITY.PopulationStatus),
                 (obs,CITY.observedAt,Literal('2025-11-22T01:10:00',datatype=XSD.dateTime)),
                 (obs,CITY.congestionLevel,Literal('여유'))]
        try:
            for t in triples:self.engine.demo.add(t)
            run=self.run_case()
            self.assertEqual(run['evidence'][0]['observation'],str(obs))
            self.assertFalse(run['decision']['recommended'])
        finally:
            for t in triples:self.engine.demo.remove(t)

    def test_validation(self):
        for value in [[],{'source':'external'},{'level':'unknown'},{'rules':{'R1':'true','R2':True}}]:
            with self.subTest(value=value),self.assertRaises(ValueError): self.engine.evaluate(value)

    def test_http_runs_real_rules_and_guards_requests(self):
        server=make_server(0,self.engine)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        root=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(root+'/api/status') as res: status=json.load(res)
            request=Request(root+'/api/evaluate',data=b'{}',headers={'Content-Type':'application/json','X-Run-Token':status['token']})
            with urlopen(request) as res: self.assertEqual(json.load(res)['counts']['recommendations'],1)
            for req,code in [(Request(root+'/api/evaluate',data=b'{}'),403),
                             (Request(root+'/api/evaluate',data=b'{}',headers={'X-Run-Token':status['token'],'Origin':'https://example.com'}),403),
                             (Request(root+'/05-dss/engine.py'),404),
                             (Request(root+'/api/status',headers={'Host':'evil.test'}),403)]:
                with self.assertRaises(HTTPError) as caught:urlopen(req)
                self.assertEqual(caught.exception.code,code)
                caught.exception.close()
        finally: server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
