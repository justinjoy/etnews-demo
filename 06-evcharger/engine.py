"""Aggregate original RDF with SPARQL and filter by geodesic distance."""
import math
from pathlib import Path
import re
import threading
import time
from rdflib import Graph, Namespace, Literal, XSD

HERE = Path(__file__).resolve().parent
CITY = Namespace('http://k.fc/onto/city#')
GEO = Namespace('http://www.opengis.net/ont/geosparql#')
EV = Namespace('https://example.org/ev-demo#')
DEFAULTS = {'radius': 500, 'min_available': 1, 'max_busy': 100, 'public_only': True}


def point(wkt):
    match = re.fullmatch(r'\s*POINT\s*\(\s*([-+\d.eE]+)\s+([-+\d.eE]+)\s*\)\s*', str(wkt))
    if not match: raise ValueError('지원하지 않는 좌표 형식입니다.')
    lon, lat = map(float, match.groups())
    if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError('좌표 범위가 올바르지 않습니다.')
    return lon, lat


def distance_and_offset(origin, target):
    lon1, lat1, lon2, lat2 = map(math.radians, (*origin, *target))
    dl = lon2 - lon1
    a = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dl/2)**2
    distance = 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, a))))
    bearing = math.atan2(math.sin(dl)*math.cos(lat2), math.cos(lat1)*math.sin(lat2)-math.sin(lat1)*math.cos(lat2)*math.cos(dl))
    return distance, distance*math.sin(bearing), distance*math.cos(bearing)


def validate(config):
    if not isinstance(config, dict): raise ValueError('JSON 객체가 필요합니다.')
    if set(config) - set(DEFAULTS): raise ValueError('알 수 없는 검색 조건입니다.')
    values = DEFAULTS | config
    for name, lower, upper in [('radius',100,1000),('min_available',1,10),('max_busy',0,100)]:
        value = values[name]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f'{name}: {lower}~{upper} 범위의 정수를 입력하세요.')
    if type(values['public_only']) is not bool: raise ValueError('이용 제한 필터는 참/거짓 값이어야 합니다.')
    return values


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.graph = Graph().parse(HERE / 'data/gangnam.ttl', format='turtle')
        self.queries = {key: (HERE / f'queries/{key}.rq').read_text(encoding='utf-8') for key in ('center','stations','chargers')}
        centers = list(self.graph.query(self.queries['center']))
        if len(centers) != 1: raise ValueError('강남역 2호선 중심 좌표를 유일하게 찾을 수 없습니다.')
        center = centers[0].asdict()
        origin = point(center['wkt'])
        self.center = {'name':'강남역', 'line':'2호선', 'iri':str(center['station']), 'lon':origin[0], 'lat':origin[1]}
        self.positions = {}
        self.unlocated = []
        for station in self.graph.objects(CITY.Area_POI014, CITY.hasEVChargerStation):
            try:
                wkt = self.graph.value(self.graph.value(station, GEO.hasGeometry), GEO.asWKT)
                target = point(wkt)
            except (ValueError, TypeError):
                self.unlocated.append(str(station)); continue
            d, x, y = distance_and_offset(origin, target)
            self.positions[str(station)] = {'lon':target[0], 'lat':target[1], 'distance':d, 'east':x, 'north':y}
            self.graph.add((station, EV.distanceMeters, Literal(d, datatype=XSD.double)))
        self.chargers = {}
        for row in self.graph.query(self.queries['chargers']):
            values = {str(k):str(v) for k,v in row.asdict().items()}
            self.chargers.setdefault(values['station'],[]).append(values)
        self.all_stations = self._read(self.queries['stations'].replace('# FILTERS',''))
        self.latest = None

    def _read(self, query):
        result = []
        for row in self.graph.query(query):
            item = {str(k):str(v) for k,v in row.asdict().items()}
            for key in ('total','busy','available','other'): item[key] = int(item[key])
            for key in ('distance','busyRatio','maxOutput'): item[key] = float(item[key])
            item['accessConflict'] = item.get('accessConflict') == 'true'
            item.update(self.positions[item['station']])
            item['chargers'] = self.chargers[item['station']]
            result.append(item)
        return result

    def status(self):
        return {'center':self.center, 'defaults':DEFAULTS, 'station_count':len(self.all_stations),
                'charger_count':sum(s['total'] for s in self.all_stations), 'unlocated':self.unlocated,
                'queries':self.queries, 'latest':self.latest}

    def search(self, config):
        filters = validate(config)
        with self.lock:
            started = time.perf_counter()
            clauses = [f"FILTER(?distance <= {filters['radius']})", f"FILTER(?available >= {filters['min_available']})",
                       f"FILTER(?busyRatio <= {filters['max_busy']} / 100.0)"]
            if filters['public_only']: clauses.append('FILTER(COALESCE(?access, "") = "N")')
            query = self.queries['stations'].replace('# FILTERS','\n  '.join(clauses))
            candidates = self._read(query)
            ids = {row['station'] for row in candidates}
            stations = []
            for original in self.all_stations:
                row = dict(original)
                reasons = []
                if row['distance'] > filters['radius']: reasons.append('검색 반경 밖')
                if row['available'] < filters['min_available']: reasons.append('가용 대수 부족')
                if row['busyRatio'] > filters['max_busy']/100: reasons.append('충전 중 비율 초과')
                if filters['public_only'] and row.get('access') != 'N': reasons.append('이용 제한 또는 정보 없음')
                row.update(matched=row['station'] in ids, reasons=reasons)
                stations.append(row)
            output = {'filters':filters, 'center':self.center, 'stations':stations,
                      'candidates':[s for s in stations if s['matched']], 'query':query,
                      'stats':{'stations':len(stations), 'within_radius':sum(s['distance']<=filters['radius'] for s in stations),
                               'matched':len(candidates), 'available':sum(s['available'] for s in candidates),
                               'chargers':sum(s['total'] for s in stations), 'unlocated':len(self.unlocated)},
                      'ms':round((time.perf_counter()-started)*1000,2)}
            self.latest = output
            return output
