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

# 공식기관 보도자료/공지. Google News RSS에서 기관 공식 도메인으로만 제한해 수집합니다.
# (기관 사이트의 HTML 구조 변경에 덜 취약하고, 결과 링크는 공식 원문으로 연결됩니다.)
OFFICIAL_QUERIES = [
    ("FIU", '("자금세탁" OR AML OR CFT OR "의심거래" OR STR OR "특정금융정보법" OR "가상자산사업자" OR "트래블룰" OR "제재") site:kofiu.go.kr'),
    ("금융위원회", '("자금세탁" OR AML OR CFT OR FIU OR "금융정보분석원" OR "의심거래" OR STR OR "특정금융정보법" OR "가상자산사업자" OR "트래블룰") site:fsc.go.kr'),
    ("금융감독원", '("자금세탁" OR AML OR CFT OR FIU OR "보이스피싱" OR "대포통장" OR "가상자산" OR "불법금융" OR "자금세탁방지") site:fss.or.kr'),
]


# 공식기관 직접 수집 경로
# 금융위원회는 공식 RSS를 제공하므로 Google News를 거치지 않고 직접 수집합니다.
FSC_RSS_URL = "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111"

# FIU는 공식 보도자료 원문 페이지를 직접 확인합니다.
# 목록 페이지가 동적 렌더링되는 경우가 있어 최근 번호 구간을 가볍게 순회하고,
# 실패 시 기존 Google News 공식도메인 검색을 보조 경로로 사용합니다.
FIU_VIEW_URL = "https://www.kofiu.go.kr/kor/notification/report_view.do?ntcnYardOrdrNo={num}&seCd=0001"
FIU_SCAN_MIN = 360
FIU_SCAN_MAX = 430

OFFICIAL_KEEP = [
    '자금세탁','돈세탁','aml','cft','fiu','금융정보분석원','의심거래','str',
    '특정금융정보법','가상자산','가상자산사업자','vasp','트래블룰','고객확인',
    '범죄수익','테러자금','제재','보이스피싱','대포통장','불법금융','환치기',
    '검사','감독','제도이행평가','위험평가','고위험','제재공시'
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
    with urllib.request.urlopen(req, timeout=12) as resp:
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
            'link': resolve_official_detail(source_name, title, it.findtext('link') or ''),
            'tags': classify(title),
            'collection_method': 'domain_search',
        })
    return out



def strip_html(s):
    s = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', s or '')
    s = re.sub(r'(?s)<[^>]+>', ' ', s)
    s = html_unescape(s)
    return re.sub(r'\s+', ' ', s).strip()

def html_unescape(s):
    import html as _html
    return _html.unescape(s or '')

def fetch_url(url, timeout=12):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 K-AML-News/3.3'
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def parse_fsc_rss():
    """금융위원회 공식 RSS 직접 수집"""
    data = fetch_url(FSC_RSS_URL)
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:150]:
        title = clean_title(it.findtext('title') or '')
        link = (it.findtext('link') or '').strip()
        date = (it.findtext('pubDate') or '').strip()
        if not title or not official_relevant(title):
            continue
        source_name = 'FIU' if ('FIU' in title.upper() or '금융정보분석원' in title) else '금융위원회'
        detail_link = resolve_official_detail(source_name, title, link)
        if not detail_link:
            continue
        out.append({
            'region': '공식자료',
            'query': source_name,
            'official_source': source_name,
            'title': title,
            'source': source_name,
            'date': date,
            'link': detail_link,
            'tags': classify(title),
            'collection_method': 'direct_rss',
        })
    return out

def parse_fiu_page(num):
    """FIU 보도자료 상세 페이지를 직접 읽어 제목/게시일을 추출"""
    url = FIU_VIEW_URL.format(num=num)
    try:
        raw = fetch_url(url, timeout=12).decode('utf-8', errors='ignore')
    except Exception:
        return None

    # 존재하지 않는 글/안내 팝업 계열은 제외
    if '보도자료' not in raw:
        return None

    # og:title 우선, 없으면 h태그/문서 제목 계열을 보조로 사용
    title = ''
    pats = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
        r'<h[1-3][^>]*>(.*?)</h[1-3]>',
        r'<title[^>]*>(.*?)</title>',
    ]
    for pat in pats:
        m = re.search(pat, raw, re.I | re.S)
        if m:
            cand = strip_html(m.group(1))
            if cand and cand not in ('금융정보분석원', '보도자료'):
                title = cand
                break

    # 본문에서 "[보도자료]" 제목이 더 잘 잡히는 경우 보정
    text = strip_html(raw)
    m2 = re.search(r'(\[보도자료\][^.]{8,180})', text)
    if m2:
        cand = m2.group(1).strip()
        if len(cand) > len(title):
            title = cand

    if not title or not official_relevant(title):
        return None

    date = ''
    dm = re.search(r'(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})', text)
    if dm:
        date = f"{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}T00:00:00+09:00"

    return {
        'region': '공식자료',
        'query': 'FIU',
        'official_source': 'FIU',
        'title': clean_title(title),
        'source': 'FIU',
        'date': date,
        'link': resolve_official_detail('FIU', title, url),
        'tags': classify(title),
        'collection_method': 'direct_page',
    }

def fetch_fiu_direct():
    out = []
    # 최근 구간 우선. 90일 보관이므로 과도한 전체 스캔은 하지 않음.
    for num in range(FIU_SCAN_MAX, FIU_SCAN_MIN - 1, -1):
        item = parse_fiu_page(num)
        if item:
            out.append(item)
        # 기관 사이트 부하를 줄이기 위해 간격을 둠
        time.sleep(0.10)
    return out


FILE_EXT_RE = re.compile(r'\.(?:pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip)(?:[?#].*)?$', re.I)

def is_file_link(url):
    if not url:
        return False
    u = url.strip().lower()
    return bool(FILE_EXT_RE.search(u)) or any(x in u for x in [
        'filedownload', 'downloadfile', 'atchfile', 'attachment', 'bbsfile'
    ])

def google_news_real_url(url):
    """Google News redirect URL은 그대로 두되 다운로드 링크면 사용하지 않는다."""
    return url if url and not is_file_link(url) else ''

def _norm_match_text(s):
    s = strip_html(s or '').lower()
    s = re.sub(r'\[보도자료\]|\(보도자료\)|보도자료', ' ', s)
    s = re.sub(r'[^0-9a-z가-힣]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _similarity(a, b):
    a = _norm_match_text(a)
    b = _norm_match_text(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    A, B = set(a.split()), set(b.split())
    j = len(A & B) / max(1, len(A | B))
    # 긴 제목은 앞/뒤 부제가 달라도 핵심 문구가 포함될 수 있음
    contain = 0.92 if (a in b or b in a) and min(len(a), len(b)) >= 18 else 0.0
    return max(j, contain)

def _extract_best_detail(raw_html, target_title, base_url, href_pattern):
    """검색결과 HTML에서 제목이 가장 가까운 '상세 본문' 링크만 고른다."""
    from urllib.parse import urljoin
    candidates = []
    for m in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw_html or '', re.I | re.S):
        href, label_html = m.group(1), m.group(2)
        if not re.search(href_pattern, href, re.I):
            continue
        label = strip_html(label_html)
        score = _similarity(target_title, label)
        if score >= 0.48:
            candidates.append((score, urljoin(base_url, html_unescape(href)), label))
    if not candidates:
        return ''
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

_DETAIL_CACHE = {}

def search_fsc_detail(title):
    """금융위 보도자료 검색결과에서 해당 제목의 실제 상세페이지를 찾는다."""
    key = ('FSC', _norm_match_text(title))
    if key in _DETAIL_CACHE:
        return _DETAIL_CACHE[key]
    query = re.sub(r'\s+', ' ', clean_title(title)).strip()
    # 검색 안정성을 위해 지나치게 긴 부제는 앞부분 위주로 검색
    query = query[:110]
    params = urllib.parse.urlencode({'srchKey':'sj', 'srchText':query})
    url = 'https://www.fsc.go.kr/no010101?' + params
    try:
        raw = fetch_url(url, timeout=10).decode('utf-8', errors='ignore')
        found = _extract_best_detail(
            raw, title, 'https://www.fsc.go.kr',
            r'/no010101/\d+(?:[?"\']|$)'
        )
    except Exception:
        found = ''
    _DETAIL_CACHE[key] = found
    return found

def search_fss_detail(title):
    """금감원 보도자료 목록 검색에서 해당 제목의 실제 view.do 상세페이지를 찾는다."""
    key = ('FSS', _norm_match_text(title))
    if key in _DETAIL_CACHE:
        return _DETAIL_CACHE[key]
    q = re.sub(r'\s+', ' ', clean_title(title)).strip()[:100]
    attempts = [
        {'menuNo':'200218','searchCnd':'1','searchWrd':q},
        {'menuNo':'200218','searchCnd':'SJ','searchWrd':q},
        {'menuNo':'200218','searchCnd':'all','searchWrd':q},
    ]
    found = ''
    for params_dict in attempts:
        url = 'https://www.fss.or.kr/fss/bbs/B0000188/list.do?' + urllib.parse.urlencode(params_dict)
        try:
            raw = fetch_url(url, timeout=8).decode('utf-8', errors='ignore')
            found = _extract_best_detail(
                raw, title, 'https://www.fss.or.kr',
                r'/fss/bbs/B0000188/(?:view\.do|[^"\']*view[^"\']*)'
            )
            if found:
                break
        except Exception:
            pass
    _DETAIL_CACHE[key] = found
    return found

def known_exact_detail(source_name, candidate):
    """이미 확보한 URL이 실제 상세 본문 형식인지 확인."""
    u = (candidate or '').strip()
    low = u.lower()
    if not u or is_file_link(u):
        return ''
    if source_name == 'FIU':
        if re.search(r'kofiu\.go\.kr/kor/notification/report_view\.do\?', low):
            return u
        # FIU 보도자료의 금융위 공식 미러 상세페이지도 허용
        if re.search(r'fsc\.go\.kr/no010101/\d+', low):
            return u
    elif source_name == '금융위원회':
        if re.search(r'fsc\.go\.kr/no010101/\d+', low):
            return u
    elif source_name == '금융감독원':
        if ('fss.or.kr' in low and re.search(r'(view\.do|/view/)', low)):
            return u
        # 금감원 보도자료가 DART 보도자료 상세페이지로 제공되는 경우
        if re.search(r'dart\.fss\.or\.kr/dsaa003/selectbodomain\.ax\?seqno=\d+', low):
            return u
    return ''

def resolve_official_detail(source_name, title, candidate=''):
    """
    제목 클릭용 URL은 '정확한 해당 보도자료 상세페이지'만 반환.
    목록/첨부파일/검색결과 URL은 반환하지 않는다.
    """
    exact = known_exact_detail(source_name, candidate)
    if exact:
        return exact

    # FIU 자료는 금융위 보도자료 게시판에도 공식 게재되는 경우가 많아
    # FIU 자체 상세 URL을 확보하지 못하면 금융위 공식 상세페이지를 제목으로 매칭한다.
    if source_name in ('FIU', '금융위원회'):
        return search_fsc_detail(title)

    if source_name == '금융감독원':
        return search_fss_detail(title)

    return ''

def official_relevant(title):
    t = (title or '').lower()
    return any(k.lower() in t for k in OFFICIAL_KEEP)

def fetch_official_source(source_name, query):
    """Collect official institution materials via Google News RSS domain-restricted search."""
    params = urllib.parse.urlencode({
        'q': query, 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'
    })
    url = 'https://news.google.com/rss/search?' + params
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 K-AML-News/3.3'
    })
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        raw_title = it.findtext('title') or ''
        title = clean_title(raw_title)
        if not title or not official_relevant(title):
            continue
        detail_link = resolve_official_detail(source_name, title, it.findtext('link') or '')
        if not detail_link:
            continue
        out.append({
            'region': '공식자료',
            'query': source_name,
            'official_source': source_name,
            'title': title,
            'source': source_name,
            'date': it.findtext('pubDate') or '',
            'link': detail_link,
            'tags': classify(title),
            'collection_method': 'domain_search',
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

    official_new = []
    official_status = []

    # 1) 금융위원회 공식 RSS 직접 수집
    try:
        items = parse_fsc_rss()
        official_new.extend(items)
        official_status.append({'feed': '금융위원회 공식 RSS', 'ok': True, 'count': len(items), 'method': 'direct_rss'})
        print('OFFICIAL DIRECT FSC', len(items))
    except Exception as e:
        official_status.append({'feed': '금융위원회 공식 RSS', 'ok': False, 'count': 0, 'error': str(e)[:180], 'method': 'direct_rss'})
        print('OFFICIAL DIRECT FSC ERROR', e)

    # 2) FIU는 금융위 공식 RSS + FIU 공식도메인 보조검색으로 수집합니다.
    #    예전처럼 FIU 게시물 번호를 수십 개씩 직접 조회하지 않아 실행시간을 크게 줄였습니다.

    # 3) FIU·금감원 및 직접수집 누락 보완용 Google News 공식도메인 검색
    #    직접수집 결과와 제목 기준으로 중복제거되므로 보조 경로로만 사용됩니다.
    for source_name, query in OFFICIAL_QUERIES:
        try:
            items = fetch_official_source(source_name, query)
            official_new.extend(items)
            official_status.append({'feed': source_name + ' 보조검색', 'ok': True, 'count': len(items), 'method': 'domain_search'})
            print('OFFICIAL FALLBACK', source_name, len(items))
        except Exception as e:
            official_status.append({'feed': source_name + ' 보조검색', 'ok': False, 'count': 0, 'error': str(e)[:180], 'method': 'domain_search'})
            print('OFFICIAL FALLBACK ERROR', source_name, e)
        time.sleep(0.25)

    # 기존 저장 데이터를 합쳐 최근 90일치를 유지
    existing = []
    existing_official = []
    p = Path('news.json')
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding='utf-8'))
            existing = old.get('items', [])
            existing_official = old.get('official_items', [])
        except Exception:
            existing = []
            existing_official = []

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

    def sanitize_official_row(x):
        x = dict(x)
        src = x.get('official_source') or x.get('source') or ''
        if x.get('region') == '공식자료' or src in ('FIU','금융위원회','금융감독원'):
            x['link'] = known_exact_detail(src, x.get('link',''))
        return x

    def clean_collection(rows, limit):
        seen = set()
        deduped = []
        for x in rows:
            d = parse_dt(x.get('date'))
            if d and d < cutoff:
                continue
            k2 = key_title(x.get('title',''))
            lk = x.get('link','')
            # Google News redirect links can differ for the same official title,
            # so title is the primary dedupe key here.
            if k2 and ('TITLE', k2) in seen:
                continue
            if lk and ('LINK', lk) in seen:
                continue
            if k2: seen.add(('TITLE', k2))
            if lk: seen.add(('LINK', lk))
            deduped.append(x)
        deduped.sort(
            key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc),
            reverse=True
        )
        return deduped[:limit]

    deduped = clean_collection(all_items + existing, 1200)
    official_rows = [sanitize_official_row(x) for x in official_new] + [sanitize_official_row(x) for x in existing_official]
    official_rows = [x for x in official_rows if x.get('link')]
    official_deduped = clean_collection(official_rows, 400)

    payload = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'retention_days': 90,
        'feed_status': status,
        'official_status': official_status,
        'count': len(deduped),
        'official_count': len(official_deduped),
        'items': deduped,
        'official_items': official_deduped
    }
    Path('news.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

if __name__ == '__main__':
    main()
