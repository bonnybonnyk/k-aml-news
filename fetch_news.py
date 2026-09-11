#!/usr/bin/env python3
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
import json, re, html, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

QUERIES = [
    ("자금세탁", '"자금세탁" OR "돈세탁"'),
    ("범죄수익", '"범죄수익" OR "범죄수익은닉" OR "범죄수익 환수"'),
    ("FIU·STR", '"금융정보분석원" OR FIU OR "의심거래보고" OR STR OR "자금세탁방지"'),
    ("가상자산 AML", '("가상자산" OR 암호화폐 OR 코인 OR USDT OR 테더) ("자금세탁" OR 범죄 OR 불법 OR 사기 OR 환치기)'),
    ("지갑·해외거래소", '("개인지갑" OR "해외거래소" OR "해외 거래소") (가상자산 OR 코인 OR 범죄)'),
    ("트래블룰", '"트래블룰" OR VASP OR "가상자산사업자"'),
    ("보이스피싱", '"보이스피싱" OR "대포통장" OR "사기이용계좌"'),
    ("투자사기", '("투자사기" OR "리딩방" OR "로맨스스캠" OR "코인사기") (자금 OR 계좌 OR 가상자산)'),
    ("환치기·외환", '"환치기" OR "불법외환" OR "외국환거래법 위반" OR "불법 송금"'),
    ("마약자금", '(마약 OR 필로폰 OR 대마) (자금 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("불법도박", '("불법도박" OR "온라인도박" OR "도박사이트") (자금 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("탈세", '(탈세 OR "조세포탈" OR "역외탈세") (차명 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("횡령·배임", '(횡령 OR 배임) ("범죄수익" OR 자금 OR 계좌 OR 은닉)'),
    ("차명계좌", '"차명계좌" OR "차명 거래" OR "명의대여"'),
    ("제재·테러자금", '"테러자금" OR "제재 회피" OR "대북제재" OR "북한 가상자산"'),
]

HARD_NEWS = [
    '자금세탁','돈세탁','범죄수익','범죄수익은닉','의심거래','str','적발','검거','기소','수사',
    '제재','압수','구속','송치','불법','사기','보이스피싱','마약','도박','환치기','탈세','횡령',
    '배임','처벌','과태료','영장','피해','범죄','위반','FIU','테러자금','제재위반'
]

# 업무 실익이 낮은 홍보/캠페인/교육/CSR성 기사
PROMO = [
    '출시','론칭','오픈','선보여','이벤트','프로모션','캠페인','브랜드','마케팅','고객 혜택',
    '무료 체험','신제품','업무협약','mou','파트너십','제휴','협력 강화','세미나','포럼',
    '컨퍼런스','웨비나','교육 실시','교육 개최','특별 교육','예방 교육','특강','설명회',
    '수상','선정','인증 획득','감사장','표창','기념','초청','참가','부스','전시','후원',
    '지원 나서','무료 지원','보험 지원','무료 보험','무료가입','보상보험','기부','봉사',
    '사회공헌','채용','인재 모집','솔루션 출시','서비스 출시','플랫폼 출시','리뉴얼',
    '업데이트','이벤트 진행','혜택 제공','예방 홍보','홍보에 기여','홍보 활동'
]

# 사건·수사·제재·수법·자금흐름·제도변경 등 실무상 가치가 높은 신호
PRACTICAL_SIGNALS = [
    '적발','검거','기소','수사','제재','압수','구속','송치','과태료','영장','범죄수익',
    '자금세탁','돈세탁','의심거래','str','보이스피싱 피해','사기 피해','피해 당',
    '피해금','피해액','환치기','탈세','횡령','배임','마약','도박','차명계좌','대포통장',
    '명의도용','가상자산 탈취','해킹','랜섬웨어','테러자금','제재위반','규정 개정',
    '법 개정','시행령','감독규정','가이드라인','FIU','FATF','AML','CFT'
]

def clean_title(t):
    t = html.unescape(t or '').strip()
    return re.sub(r'\s+-\s+[^-]{2,60}$', '', t).strip()

def source_from(item, title):
    source = item.findtext('source') or ''
    if source.strip():
        return source.strip()
    m = re.search(r'\s+-\s+([^-]+)$', title or '')
    return m.group(1).strip() if m else '뉴스'

def is_promo(title):
    t = title.lower()

    promo_hit = any(k.lower() in t for k in PROMO)
    practical_hit = any(k.lower() in t for k in PRACTICAL_SIGNALS)

    # 행사/교육/캠페인/지원/사회공헌성 기사면 원칙적으로 제외.
    # 단, 같은 제목 안에 실제 사건/수사/제재/피해/제도변경 신호가 명확하면 유지.
    if promo_hit and not practical_hit:
        return True

    # 보이스피싱 같은 범죄 키워드가 있어도 "예방교육/홍보/보험지원"이면 제외
    low_value_patterns = [
        '보이스피싱 예방', '사기 예방', '예방 특별 교육', '예방 교육',
        '무료 보험', '보상보험', '보험 무료가입', '감사장 수여',
        '홍보에 기여', '예방 홍보', '캠페인 전개', '업무협약 체결',
        '사회공헌', '피해 예방을 위한 교육'
    ]
    if any(k.lower() in t for k in low_value_patterns) and not any(
        k.lower() in t for k in ['피해 당','피해금','피해액','검거','수사','기소','적발','제재','압수']
    ):
        return True

    return False

def classify(title):
    t = title.lower()
    tags = []
    if re.search(r'가상자산|암호화폐|코인|비트코인|이더리움|usdt|테더|지갑|거래소|트래블룰|vasp', t): tags.append('가상자산')
    if re.search(r'보이스피싱|사기|리딩방|로맨스스캠|대포통장', t): tags.append('사기')
    if re.search(r'마약|필로폰|대마', t): tags.append('마약')
    if re.search(r'도박|카지노|베팅', t): tags.append('도박')
    if re.search(r'탈세|조세포탈|역외탈세', t): tags.append('탈세')
    if re.search(r'횡령|배임', t): tags.append('횡령·배임')
    if re.search(r'환치기|불법외환|외국환', t): tags.append('환치기·외환')
    if re.search(r'fiu|금융정보분석원|str|의심거래|자금세탁방지|규제|제도', t): tags.append('FIU·규제')
    if re.search(r'테러자금|제재|대북|북한', t): tags.append('제재·테러자금')
    if re.search(r'자금세탁|돈세탁|범죄수익', t): tags.append('자금세탁')
    return tags or ['AML']

def fetch_query(name, query):
    params = urllib.parse.urlencode({
        'q': query, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'
    })
    url = 'https://news.google.com/rss/search?' + params
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 K-AML-News/2.1'
    })
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        raw_title = it.findtext('title') or ''
        title = clean_title(raw_title)
        if not title or is_promo(title):
            continue
        out.append({
            'region': '국내',
            'query': name,
            'title': title,
            'source': source_from(it, raw_title),
            'date': it.findtext('pubDate') or '',
            'link': it.findtext('link') or '#',
            'tags': classify(title),
        })
    return out

def key_title(s):
    return re.sub(r'[^0-9a-z가-힣]+','', (s or '').lower())

def main():
    all_items = []
    status = []
    for name, query in QUERIES:
        try:
            items = fetch_query(name, query)
            all_items.extend(items)
            status.append({'feed': name, 'ok': True, 'count': len(items)})
            print(name, len(items))
        except Exception as e:
            status.append({'feed': name, 'ok': False, 'count': 0, 'error': str(e)[:180]})
            print('ERROR', name, e)
        time.sleep(0.4)

    # 기존 저장 뉴스도 합쳐 최근 90일치를 유지
    existing = []
    p = Path('news.json')
    if p.exists():
        try:
            existing = json.loads(p.read_text(encoding='utf-8')).get('items', [])
        except Exception:
            existing = []

    merged = all_items + existing
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)

    def parse_dt(s):
        from email.utils import parsedate_to_datetime
        if not s:
            return None
        try:
            d = parsedate_to_datetime(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)
        except Exception:
            try:
                d = datetime.fromisoformat(str(s).replace('Z','+00:00'))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return d.astimezone(timezone.utc)
            except Exception:
                return None

    # 90일보다 오래된 뉴스 제거 + 정확 중복 제거
    seen = set()
    deduped = []
    for x in merged:
        d = parse_dt(x.get('date'))
        if d and d < cutoff:
            continue
        k2 = key_title(x.get('title',''))
        lk = x.get('link','')
        if lk and ('LINK', lk) in seen:
            continue
        if k2 and ('TITLE', k2) in seen:
            continue
        if lk: seen.add(('LINK', lk))
        if k2: seen.add(('TITLE', k2))
        deduped.append(x)

    deduped.sort(key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True)

    payload = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'retention_days': 90,
        'feed_status': status,
        'count': len(deduped),
        'items': deduped[:1200]
    }
    Path('news.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    main()
