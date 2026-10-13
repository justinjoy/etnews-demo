#!/usr/bin/env python3
# csv_to_cctv_ttl.py
# CSV -> TTL 변환 (GeoSPARQL, EPSG:4326, WKT 포함)

import argparse, csv, re, sys, pathlib

PFX = {
    "cctv":  "http://k.fc/onto/cctv#",
    "geo":   "http://www.opengis.net/ont/geosparql#",
    "rdfs":  "http://www.w3.org/2000/01/rdf-schema#",
    "dct":   "http://purl.org/dc/terms/",
}
CRS_EPSG4326 = "<http://www.opengis.net/def/crs/EPSG/0/4326>"

# 후보 컬럼명 사전
CAND = {
    "id":  ["cctvid","id","camid","camera_id"],
    "name":["cctvname","name","label","title","cctv_nm"],
    "lon": ["xcoord","lon","longitude","x","lng"],
    "lat": ["ycoord","lat","latitude","y"],
}

def norm(s): return (s or "").strip()
def keyset(row):
    # 소문자, 공백/밑줄 제거
    return { re.sub(r"[\s_]+","",k.strip().lower()): k for k in row.keys() }

def pick(colmap, kind):
    for cand in CAND[kind]:
        k = re.sub(r"[\s_]+","",cand)
        if k in colmap: return colmap[k]
    return None

def esc_lit(s):
    return s.replace('\\','\\\\').replace('"','\\"')

def mint_uri(id_str):
    base = re.sub(r"[^A-Za-z0-9_\\-]","_", id_str.strip())
    if not base: base = "unknown"
    # URI는 첫 글자가 숫자여도 Turtle QNAME로 사용할 것이므로 접두 추가
    if base[0].isdigit(): base = "CCTV_" + base
    return f"cctv:{base}"

def detect_delimiter(sample_path):
    with open(sample_path,'r',encoding='utf-8-sig',newline='') as f:
        snip = f.read(4096)
    try:
        return csv.Sniffer().sniff(snip).delimiter
    except Exception:
        return ','

def main():
    ap = argparse.ArgumentParser(description="CSV의 CCTV 좌표를 TTL(GeoSPARQL)로 변환")
    ap.add_argument("csv_path", help="입력 CSV 경로")
    ap.add_argument("-o","--out", default="cctv.ttl", help="출력 TTL 경로")
    ap.add_argument("--base", default=PFX["cctv"], help="cctv: prefix IRI")
    args = ap.parse_args()

    delim = detect_delimiter(args.csv_path)

    with open(args.csv_path,'r',encoding='utf-8-sig',newline='') as f, \
         open(args.out,'w',encoding='utf-8') as w:

        rdr = csv.DictReader(f, delimiter=delim)
        if not rdr.fieldnames:
            sys.exit("CSV 헤더가 필요합니다.")

        colmap = keyset({h:h for h in rdr.fieldnames})
        cid  = pick(colmap,"id")
        cname= pick(colmap,"name")
        clon = pick(colmap,"lon")
        clat = pick(colmap,"lat")

        missing = [k for k,v in {"ID":cid,"NAME":cname,"LON":clon,"LAT":clat}.items() if v is None]
        if missing:
            sys.exit(f"필수 컬럼을 찾을 수 없습니다: {', '.join(missing)}  "
                     f"(허용 예: id→{CAND['id']}, name→{CAND['name']}, "
                     f"lon→{CAND['lon']}, lat→{CAND['lat']})")

        # Prefix 헤더
        w.write(f"@prefix cctv: <{args.base}> .\n")
        w.write(f"@prefix geo:  <{PFX['geo']}> .\n")
        w.write(f"@prefix rdfs: <{PFX['rdfs']}> .\n")
        w.write(f"@prefix dct:  <{PFX['dct']}> .\n\n")

        n=0
        for row in rdr:
            rid  = norm(row[cid])
            name = norm(row[cname])
            try:
                lon  = float(norm(row[clon]))
                lat  = float(norm(row[clat]))
            except ValueError:
                # 좌표가 숫자가 아니면 스킵
                continue

            subj = mint_uri(rid or f"row{n+1}")
            label = esc_lit(name or rid or f"CCTV-{n+1}")

            w.write(f"{subj} a cctv:CCTV ;\n")
            w.write(f'    dct:identifier "{esc_lit(rid)}" ;\n')
            w.write(f'    rdfs:label "{label}"@ko ;\n')
            w.write( "    geo:hasGeometry [\n")
            w.write( "        a geo:Point ;\n")
            # 한 줄 WKT + EPSG:4326 IRI
            w.write(f'        geo:asWKT "{CRS_EPSG4326} POINT({lon} {lat})"^^geo:wktLiteral\n')
            w.write( "    ] .\n\n")
            n+=1

        sys.stderr.write(f"작성 완료: {args.out}  ({n} triples subjects)\n")

if __name__ == "__main__":
    main()
