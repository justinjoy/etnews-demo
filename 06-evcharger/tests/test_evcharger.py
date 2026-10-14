import math
from pathlib import Path
import sys
import threading
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine,distance_and_offset,point,validate
from server import make_server

class EVTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.engine=Engine()

    def test_original_counts_are_not_multiplied_by_metadata(self):
        stations=self.engine.all_stations
        self.assertEqual(len(stations),37)
        self.assertEqual(len({s['station'] for s in stations}),37)
        self.assertEqual(sum(s['total'] for s in stations),117)
        for s in stations:
            self.assertEqual(s['total'],len(s['chargers']))
            self.assertEqual(s['total'],s['available']+s['busy']+s['other'])
            self.assertEqual(s['available'],sum(c.get('status')=='사용가능' for c in s['chargers']))

    def test_source_center_and_metric_geometry(self):
        self.assertEqual((self.engine.center['lon'],self.engine.center['lat']),(127.027912,37.49799))
        self.assertEqual(distance_and_offset((0,0),(0,0)),(0,0,0))
        d,x,y=distance_and_offset((0,0),(0,1))
        self.assertAlmostEqual(d,111195.0802,places=3)
        self.assertAlmostEqual(x,0,places=5);self.assertGreater(y,0)
        d,x,y=distance_and_offset((0,0),(1,0))
        self.assertGreater(x,0);self.assertAlmostEqual(y,0,places=5)
        for s in self.engine.all_stations:self.assertAlmostEqual(math.hypot(s['east'],s['north']),s['distance'],places=6)

    def test_radius_changes_actual_sparql_candidates(self):
        small=self.engine.search({'radius':250});large=self.engine.search({'radius':1000})
        a={s['station'] for s in small['candidates']};b={s['station'] for s in large['candidates']}
        self.assertTrue(a < b)
        self.assertEqual(len(a),9);self.assertEqual(len(b),24)
        self.assertIn('FILTER(?distance <= 250)',small['query'])
        self.assertTrue(all(s['distance']<=250 for s in small['candidates']))
        distances=[s['distance'] for s in large['candidates']]
        self.assertEqual(distances,sorted(distances))

    def test_original_dss_availability_and_busy_filter(self):
        result=self.engine.search({'radius':1000,'public_only':False,'max_busy':50})
        self.assertEqual(len(result['candidates']),31)
        self.assertTrue(all(s['available']>=1 and s['busyRatio']<=.5 for s in result['candidates']))
        self.assertEqual(len(self.engine.search({'radius':1000,'public_only':False})['candidates']),34)

    def test_restriction_conflicts_are_not_public(self):
        result=self.engine.search({'radius':1000})
        conflicts=[s for s in result['stations'] if s['accessConflict']]
        self.assertEqual(len(conflicts),3)
        self.assertTrue(all(s['access']=='Y' and not s['matched'] for s in conflicts))
        self.assertTrue(all(s['access']=='N' for s in result['candidates']))

    def test_minimum_and_empty_results(self):
        result=self.engine.search({'min_available':5,'radius':1000})
        self.assertTrue(all(s['available']>=5 for s in result['candidates']))
        result=self.engine.search({'radius':100,'min_available':10})
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['stats']['available'],0)
        self.assertTrue(all(s['reasons'] for s in result['stations']))

    def test_other_statuses_and_available_power(self):
        result=self.engine.search({'radius':1000,'public_only':False})
        for s in result['stations']:
            power=max([float(c['output']) for c in s['chargers'] if c.get('status')=='사용가능' and c.get('output')]+[0])
            self.assertEqual(s['maxOutput'],power)
            self.assertEqual(s['matched'],not s['reasons'])
        hotel=next(s for s in result['stations'] if s['name']=='강남아르누보호텔')
        self.assertEqual(hotel['available'],0)
        self.assertFalse(hotel['matched'])

    def test_query_results_and_returned_candidates_match(self):
        result=self.engine.search({'radius':500,'max_busy':25})
        raw={str(row.station) for row in self.engine.graph.query(result['query'])}
        self.assertEqual(raw,{s['station'] for s in result['candidates']})

    def test_validation(self):
        for config in [{'radius':99},{'radius':float('nan')},{'radius':True},{'radius':'500'},{'max_busy':101},{'min_available':0},{'public_only':'false'},[],{'unknown':1}]:
            with self.subTest(config=config),self.assertRaises(ValueError):validate(config)
        for wkt in ['POINT (NaN 1)','POINT (181 2)','POINT (1 91)','LINESTRING (1 2,3 4)']:
            with self.subTest(wkt=wkt),self.assertRaises(ValueError):point(wkt)

    def test_http_search_and_request_guards(self):
        server=make_server(0,self.engine);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/api/status') as response:token=json.load(response)['token']
            request=Request(base+'/api/search',data=b'{"radius":250}',headers={'X-Run-Token':token,'Content-Type':'application/json'})
            with urlopen(request) as response:self.assertEqual(json.load(response)['stats']['matched'],9)
            for request,code in [(Request(base+'/api/search',data=b'{}'),403),(Request(base+'/06-evcharger/engine.py'),404),
                                 (Request(base+'/api/search',data=b'{}',headers={'X-Run-Token':token,'Origin':'https://example.com'}),403)]:
                with self.assertRaises(HTTPError) as caught:urlopen(request)
                self.assertEqual(caught.exception.code,code);caught.exception.close()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
