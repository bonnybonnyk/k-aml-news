#!/usr/bin/env python3
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
import json, re, html, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

RETENTION_DAYS = 90
KST = timezone(timedelta(hours=9))

# 국내 뉴스: 매 실행마다 최근 24시간을 다시 검색하고, news.json에는 90일 보관.
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

# 금융위/FIU 공식 보도자료 직접검색용 키워드.
# 너무 잘게 쪼개면 요청 수가 폭증하므로, 실무적으로 빠짐을 줄이면서도 10개로 제한.
FSC_SEARCH_KEYWORDS = [
    "금융정보분석원",
    "자금세탁",
    "가상자산사업자",
    "가상자산",
    "특정금융정보법",
    "의심거래",
    "FATF",
    "범죄수익",
    "테러자금",
    "보이스피싱",
    "트래블룰",
    "불법사금융",
    "불법금융",
]

# 검색 결과를 다시 거르는 AML 관련어. 제목에 하나 이상 있어야 공식자료로 채택.
OFFICIAL_KEEP = [
    "금융정보분석원","fiu","자금세탁","자금세탁방지","aml","cft","fatf",
    "의심거래","str","고액현금거래","ctr","특정금융정보법","특금법",
    "가상자산사업자","vasp","가상자산","트래블룰","고객확인","kyc",
    "제도이행평가","위험평가","범죄수익","테러자금","제재","보이스피싱",
    "대포통장","불법금융","불법사금융","사금융","환치기","불공정거래","시세조종","미등록 영업"
]

PROMO = [
    '출시','론칭','오픈','선보여','이벤트','프로모션','캠페인','브랜드','마케팅','고객 혜택',
    '무료 체험','신제품','업무협약','mou','파트너십','제휴','협력 강화','세미나','포럼',
    '컨퍼런스','웨비나','교육 실시','교육 개최','특별 교육','예방 교육','특강','설명회',
    '수상','선정','인증 획득','감사장','표창','기념','초청','참가','부스','전시','후원',
    '지원 나서','무료 지원','보험 지원','무료 보험','무료가입','보상보험','기부','봉사',
    '사회공헌','채용','인재 모집','솔루션 출시','서비스 출시','플랫폼 출시','리뉴얼',
    '업데이트','이벤트 진행','혜택 제공','예방 홍보','홍보에 기여','홍보 활동'
]
PRACTICAL_SIGNALS = [
    '적발','검거','기소','수사','제재','압수','구속','송치','과태료','영장','범죄수익',
    '자금세탁','돈세탁','의심거래','str','피해','환치기','탈세','횡령','배임','마약','도박',
    '차명계좌','대포통장','명의도용','가상자산 탈취','해킹','랜섬웨어','테러자금',
    '제재위반','규정 개정','법 개정','시행령','감독규정','가이드라인','fiu','fatf','aml','cft'
]

FSC_BOARD_URL = "https://www.fsc.go.kr/no010101"
FSS_QUERY = '("자금세탁" OR AML OR CFT OR FIU OR "보이스피싱" OR "대포통장" OR "가상자산" OR "불법금융" OR "자금세탁방지") site:fss.or.kr'

def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 K-AML-News/4.7'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def html_unescape(s):
    return html.unescape(s or '')

def strip_html(s):
    s = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', s or '')
    s = re.sub(r'(?s)<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html_unescape(s)).strip()

def clean_title(t):
    t = html_unescape(t or '').strip()
    return re.sub(r'\s+-\s+[^-]{2,60}$', '', t).strip()

def source_from(item, raw_title):
    source = item.findtext('source') or ''
    if source.strip():
        return source.strip()
    m = re.search(r'\s+-\s+([^-]+)$', raw_title or '')
    return m.group(1).strip() if m else '뉴스'

def classify(title):
    t = (title or '').lower()
    tags = []
    if re.search(r'가상자산|암호화폐|코인|비트코인|이더리움|usdt|테더|지갑|거래소|트래블룰|vasp', t): tags.append('가상자산')
    if re.search(r'보이스피싱|사기|리딩방|로맨스스캠|대포통장', t): tags.append('사기')
    if re.search(r'마약|필로폰|대마', t): tags.append('마약')
    if re.search(r'도박|카지노|베팅', t): tags.append('도박')
    if re.search(r'탈세|조세포탈|역외탈세', t): tags.append('탈세')
    if re.search(r'횡령|배임', t): tags.append('횡령·배임')
    if re.search(r'환치기|불법외환|외국환', t): tags.append('환치기·외환')
    if re.search(r'fiu|금융정보분석원|str|의심거래|자금세탁방지|규제|제도|fatf', t): tags.append('FIU·규제')
    if re.search(r'테러자금|제재|대북|북한', t): tags.append('제재·테러자금')
    if re.search(r'자금세탁|돈세탁|범죄수익', t): tags.append('자금세탁')
    return tags or ['AML']

def is_promo(title):
    t = (title or '').lower()
    promo_hit = any(k.lower() in t for k in PROMO)
    practical_hit = any(k.lower() in t for k in PRACTICAL_SIGNALS)
    if promo_hit and not practical_hit:
        return True
    low = [
        '보이스피싱 예방','사기 예방','예방 특별 교육','예방 교육','무료 보험','보상보험',
        '보험 무료가입','감사장 수여','홍보에 기여','예방 홍보','캠페인 전개','업무협약 체결',
        '사회공헌','피해 예방을 위한 교육'
    ]
    if any(k in t for k in low) and not any(k in t for k in ['피해','검거','수사','기소','적발','제재','압수']):
        return True
    return False

def parse_dt(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=KST)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def key_title(s):
    return re.sub(r'[^0-9a-z가-힣]+', '', (s or '').lower())

def official_relevant(title):
    t = (title or '').lower()
    return any(k.lower() in t for k in OFFICIAL_KEEP)


# 국내 뉴스 품질 필터
# 사건·수사 기사에 필요한 일반 언론은 폭넓게 남기되,
# 도박/카지노 운영사이트·리퍼럴/코인 홍보성 사이트 같은 "뉴스가 아닌 출처"를 제거한다.
TRUSTED_NEWS_DOMAINS = [
    # 통신/방송
    'yna.co.kr','newsis.com','news1.kr','kbs.co.kr','imbc.com','sbs.co.kr','ytn.co.kr',
    'jtbc.co.kr','mbn.co.kr','tvchosun.com','ichannela.com','obsnews.co.kr',
    # 종합지/경제지
    'chosun.com','joongang.co.kr','donga.com','hani.co.kr','khan.co.kr','hankookilbo.com',
    'mk.co.kr','hankyung.com','sedaily.com','fnnews.com','mt.co.kr','edaily.co.kr',
    'asiae.co.kr','heraldcorp.com','bizwatch.co.kr','etoday.co.kr','ajunews.com',
    'newsway.co.kr','thebell.co.kr','dealsite.co.kr',
    # IT/가상자산 전문 매체 중 기사형 출처
    'zdnet.co.kr','etnews.com','digitaltoday.co.kr','ddaily.co.kr','bloter.net',
    'tokenpost.kr','blockmedia.co.kr','decenter.kr',
    # 지역/기타 주요 언론
    'busan.com','imaeil.com','kwnews.co.kr','kado.net','jnilbo.com','jjan.kr','jejunews.com'
]

TRUSTED_SOURCE_NAMES = [
    '연합뉴스','뉴시스','뉴스1','kbs','mbc','sbs','ytn','jtbc','mbn','tv조선','채널a',
    '조선일보','중앙일보','동아일보','한겨레','경향신문','한국일보','매일경제','한국경제',
    '서울경제','파이낸셜뉴스','머니투데이','이데일리','아시아경제','헤럴드경제',
    '비즈워치','이투데이','아주경제','뉴스웨이','더벨','딜사이트',
    '전자신문','지디넷코리아','디지털데일리','디지털투데이','블로터',
    '토큰포스트','블록미디어','디센터'
]

def trusted_news_source(source_name, domain):
    s = (source_name or '').lower().replace(' ', '')
    d = (domain or '').lower()
    if any(d == x or d.endswith('.' + x) for x in TRUSTED_NEWS_DOMAINS):
        return True
    return any(x.lower().replace(' ', '') in s for x in TRUSTED_SOURCE_NAMES)

BLOCKED_DOMAIN_TOKENS = [
    'casino','slot','slots','spin','spinkr','bet','betting','toto','sportsbook',
    'poker','gamble','gamingbonus','jackpot','roulette','baccarat',
    'coinreferral','referral','airdrop','bonus','promo'
]
BLOCKED_SOURCE_TOKENS = [
    '카지노','슬롯','토토','베팅','도박사이트','온라인카지노','바카라',
    '보너스','에어드롭','리퍼럴','추천인','프로모션'
]
ADLIKE_TITLE_TOKENS = [
    '가입 이벤트','충전 이벤트','입금 이벤트','보너스 지급','롤링','페이백',
    '추천인 코드','가입 코드','무료머니','첫충전','재충전','가입 혜택',
    '무기명 회원','무제한 지원','카지노 이벤트','슬롯 이벤트','토너먼트 이벤트'
]
NEWS_SIGNAL_TOKENS = [
    '경찰','검찰','법원','금감원','금융위','금융정보분석원','국세청','관세청',
    '적발','검거','구속','기소','송치','수사','압수','추징','징역','벌금',
    '피해','피의자','범죄','사기','자금세탁','범죄수익','환치기','불법',
    '제재','위반','수사기관','재판','선고','영장'
]

def get_source_meta(item, raw_title):
    source_el = item.find('source')
    source_name = (source_el.text or '').strip() if source_el is not None and source_el.text else ''
    source_url = (source_el.attrib.get('url') or '').strip() if source_el is not None else ''
    if not source_name:
        source_name = source_from(item, raw_title)
    domain = ''
    if source_url:
        try:
            domain = urllib.parse.urlparse(source_url).netloc.lower().split(':')[0]
        except Exception:
            domain = ''
    return source_name, source_url, domain

def low_quality_news_source(title, source_name, domain):
    t = (title or '').lower()
    s = (source_name or '').lower()
    d = (domain or '').lower()

    # 명백한 도박/카지노/리퍼럴 사이트 출처
    if any(tok in d for tok in BLOCKED_DOMAIN_TOKENS):
        return True
    if any(tok in s for tok in BLOCKED_SOURCE_TOKENS):
        return True

    # 도메인/출처가 수상하지 않더라도 제목 자체가 이벤트·가입 유도형이면 제거.
    if any(tok in t for tok in ADLIKE_TITLE_TOKENS):
        # 다만 해당 홍보/이벤트가 수사·적발된 사건을 다루는 기사면 유지.
        if not any(sig in t for sig in NEWS_SIGNAL_TOKENS):
            return True

    # 카지노/토토/슬롯이 제목 핵심인데 실제 사건·수사 신호가 하나도 없으면 제외.
    gambling_terms = ['카지노','토토','슬롯','바카라','베팅','도박사이트']
    if any(g in t for g in gambling_terms) and not any(sig in t for sig in NEWS_SIGNAL_TOKENS):
        return True

    return False


# v4.7 기사성 필터: AML 소재 자체(상품권 현금화/불법도박/USDT 등)는 차단하지 않는다.
# 대신 모집·판매·가입유도 문구와 비기사성 영역을 차단한다.
V47_NONARTICLE_HOST_PREFIXES=['stock.','search.','m.stock.','finance.']
V47_NONARTICLE_PATH_TOKENS=['/search','/board','/bbs','/community','/user','/profile']
V47_SOLICITATION_TERMS=[
    '팝니다','삽니다','판매합니다','구매합니다','가입문의','가입 문의','문의주세요',
    '추천인 코드','추천코드','가입 코드','첫충전','재충전','롤링','페이백',
    '무료머니','보너스 지급','충전 이벤트','가입 이벤트'
]
V47_CONTACT_TERMS=['텔레그램','오픈채팅','카톡 문의','카카오톡 문의']

def v47_is_nonarticle(title, source_url, source_domain):
    t=title or ''
    d=(source_domain or '').lower()
    u=(source_url or '').lower()
    if any(d.startswith(p) for p in V47_NONARTICLE_HOST_PREFIXES):
        return True
    try:
        path=urllib.parse.urlparse(u).path.lower()
    except Exception:
        path=''
    if any(tok in path for tok in V47_NONARTICLE_PATH_TOKENS):
        return True
    if re.search(r'@[A-Za-z0-9_]{4,}',t):
        return True
    if any(tok in t for tok in V47_SOLICITATION_TERMS):
        return True
    # 연락수단은 단독 언급이 기사에 나올 수도 있으므로 판매/문의 문맥과 결합할 때 차단
    if any(c in t for c in V47_CONTACT_TERMS) and re.search(r'(문의|판매|구매|가입|코드|연락)',t):
        return True
    return False

def fetch_query(name, query):
    # 최신 발견용: 최근 24시간만 재검색
    params = urllib.parse.urlencode({
        'q': query + ' when:1d',
        'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'
    })
    url = 'https://news.google.com/rss/search?' + params
    data = fetch_url(url, timeout=10)
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        raw_title = it.findtext('title') or ''
        title = clean_title(raw_title)
        if not title or is_promo(title):
            continue
        source_name, source_url, source_domain = get_source_meta(it, raw_title)
        # v4.5: Google News에 잡혔다는 이유만으로 채택하지 않는다.
        # 국내뉴스는 신뢰 출처 목록을 먼저 통과해야 하고, 그 뒤 광고/SEO 필터를 적용한다.
        if not trusted_news_source(source_name, source_domain):
            continue
        if v47_is_nonarticle(title, source_url, source_domain):
            continue
        if low_quality_news_source(title, source_name, source_domain):
            continue
        out.append({
            'region': '국내',
            'query': name,
            'title': title,
            'source': source_name,
            'source_url': source_url,
            'source_domain': source_domain,
            'date': it.findtext('pubDate') or '',
            'link': it.findtext('link') or '',
            'tags': classify(title),
            'collection_method': 'google_news_24h',
        })
    return out

def fsc_search_url(keyword, page, begin_ymd, end_ymd):
    params = urllib.parse.urlencode({
        'curPage': str(page),
        'srchKey': 'sj',
        'srchText': keyword,
        'srchBeginDt': begin_ymd,
        'srchEndDt': end_ymd,
    })
    return FSC_BOARD_URL + '?' + params

def parse_fsc_search_page(raw_html, keyword):
    """
    금융위원회 보도자료 검색결과에서 실제 /no010101/<번호> 제목 링크와 날짜를 추출.
    날짜가 확인되지 않는 행은 버려 잘못된 카드 생성을 막는다.
    """
    rows = []
    pat = re.compile(
        r'<a\b[^>]*href=["\']([^"\']*/no010101/\d+[^"\']*)["\'][^>]*>(.*?)</a>',
        re.I | re.S
    )
    matches = list(pat.finditer(raw_html))
    for i, m in enumerate(matches):
        href = html_unescape(m.group(1))
        title = clean_title(strip_html(m.group(2)))
        if not title or not official_relevant(title):
            continue
        next_pos = matches[i+1].start() if i+1 < len(matches) else min(len(raw_html), m.end()+3000)
        chunk = strip_html(raw_html[m.end():next_pos])
        dm = re.search(r'(20\d{2})[.\-/](\d{2})[.\-/](\d{2})', chunk)
        if not dm:
            continue
        date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}T00:00:00+09:00"
        detail = urllib.parse.urljoin("https://www.fsc.go.kr", href).replace('&amp;', '&')
        src = 'FIU' if ('FIU' in title.upper() or '금융정보분석원' in title) else '금융위원회'
        rows.append({
            'region': '공식자료',
            'query': keyword,
            'official_source': src,
            'title': title,
            'source': src,
            'date': date,
            'link': detail,
            'tags': classify(title),
            'collection_method': 'verified_fsc_board',
        })
    return rows

def fetch_fsc_keyword_page(keyword, page, begin_ymd, end_ymd):
    raw = fetch_url(fsc_search_url(keyword, page, begin_ymd, end_ymd), timeout=10).decode('utf-8', errors='ignore')
    return parse_fsc_search_page(raw, keyword)

def verify_fsc_detail(item):
    """
    실제 상세페이지를 1회 확인:
    - HTTP 응답 성공
    - 보도자료 제목이 페이지에 존재
    - 제목 외 본문 텍스트가 충분히 존재
    빈 FIU 페이지/엉뚱한 링크를 제거한다.
    """
    try:
        raw = fetch_url(item['link'], timeout=8).decode('utf-8', errors='ignore')
        text = strip_html(raw)
        title_key = key_title(item.get('title', ''))
        text_key = key_title(text)
        if not title_key or title_key not in text_key:
            return None
        # 제목만 있는 빈 페이지를 거르기 위한 최소 텍스트 길이
        if len(text) < 350:
            return None
        return item
    except Exception:
        return None

def dedupe(rows, limit=1000):
    seen_t, seen_l = set(), set()
    out = []
    for x in rows:
        kt = key_title(x.get('title',''))
        lk = x.get('link','')
        if kt and kt in seen_t:
            continue
        if lk and lk in seen_l:
            continue
        if kt: seen_t.add(kt)
        if lk: seen_l.add(lk)
        out.append(x)
    out.sort(key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True)
    return out[:limit]

def collect_fsc_official(backfill, cutoff, now_utc):
    """
    첫 성공 실행: 최근 90일 백필.
    이후: 최근 7일만 다시 훑어 신규/수정 자료 증분수집.
    """
    begin = (cutoff if backfill else now_utc - timedelta(days=7)).astimezone(KST).strftime('%Y-%m-%d')
    end = now_utc.astimezone(KST).strftime('%Y-%m-%d')
    max_pages = 6 if backfill else 1

    candidates = []
    status = []
    for kw in FSC_SEARCH_KEYWORDS:
        kw_rows = []
        for page in range(1, max_pages + 1):
            try:
                rows = fetch_fsc_keyword_page(kw, page, begin, end)
                kw_rows.extend(rows)
                # 검색결과가 없으면 뒤 페이지도 없음
                if not rows:
                    break
            except Exception as e:
                status.append({'feed': f'금융위/FIU:{kw}:p{page}', 'ok': False, 'count': 0, 'error': str(e)[:160]})
                break
        candidates.extend(kw_rows)
        status.append({'feed': f'금융위/FIU:{kw}', 'ok': True, 'count': len(kw_rows), 'method': 'official_board'})
        time.sleep(0.05)

    # 90일 밖 제거 + 중복 후 상세페이지 병렬 검증
    candidates = [
        x for x in dedupe(candidates, 500)
        if (parse_dt(x.get('date')) and parse_dt(x.get('date')) >= cutoff)
    ]

    verified = []
    # 상세 검증은 URL별 1회, 병렬로 실행해 첫 백필도 오래 걸리지 않도록 함.
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(verify_fsc_detail, x): x for x in candidates}
        for fut in as_completed(futs):
            try:
                x = fut.result()
                if x:
                    verified.append(x)
            except Exception:
                pass

    return dedupe(verified, 400), status

def exact_fss_detail_from_item(item):
    blob = html_unescape(ET.tostring(item, encoding='unicode'))
    try:
        blob += ' ' + urllib.parse.unquote(blob)
    except Exception:
        pass
    pats = [
        r'https?://(?:www\.)?fss\.or\.kr/[^"\'<>\s]*(?:view\.do|/view/)[^"\'<>\s]*',
        r'https?://dart\.fss\.or\.kr/dsaa003/selectBodoMain\.ax\?[^"\'<>\s]*seqno=\d+[^"\'<>\s]*'
    ]
    for pat in pats:
        m = re.search(pat, blob, re.I)
        if m:
            u = html_unescape(m.group(0)).replace('&amp;', '&')
            if not re.search(r'\.(pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip)(?:[?#]|$)', u, re.I):
                return u
    return ''

def collect_fss_incremental():
    # 금감원은 정확한 상세 URL이 RSS item 안에 들어있는 것만 채택.
    params = urllib.parse.urlencode({'q': FSS_QUERY + ' when:90d', 'hl':'ko','gl':'KR','ceid':'KR:ko'})
    data = fetch_url('https://news.google.com/rss/search?' + params, timeout=10)
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        title = clean_title(it.findtext('title') or '')
        if not title or not official_relevant(title):
            continue
        link = exact_fss_detail_from_item(it)
        if not link:
            continue
        out.append({
            'region':'공식자료','query':'금융감독원','official_source':'금융감독원',
            'title':title,'source':'금융감독원','date':it.findtext('pubDate') or '',
            'link':link,'tags':classify(title),'collection_method':'fss_exact_search'
        })
    return out

def main():
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=RETENTION_DAYS)

    p = Path('news.json')
    old = {}
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            old = {}

    existing = old.get('items', [])
    existing_official = old.get('official_items', [])

    # ---------- 국내 뉴스 ----------
    all_items, status = [], []
    for name, query in QUERIES:
        try:
            items = fetch_query(name, query)
            all_items.extend(items)
            status.append({'feed': name, 'ok': True, 'count': len(items)})
            print('NEWS', name, len(items))
        except Exception as e:
            status.append({'feed': name, 'ok': False, 'count': 0, 'error': str(e)[:180]})
            print('NEWS ERROR', name, e)
        time.sleep(0.12)

    merged_news = []
    for x in all_items + existing:
        d = parse_dt(x.get('date'))
        if d and d < cutoff:
            continue
        # 과거 버전에서 저장된 스팸/SEO 출처도 다시 정리한다.
        src_name = x.get('source','')
        src_domain = x.get('source_domain','')
        if not trusted_news_source(src_name, src_domain):
            continue
        if v47_is_nonarticle(x.get('title',''), x.get('source_url',''), src_domain):
            continue
        if low_quality_news_source(x.get('title',''), src_name, src_domain):
            continue
        merged_news.append(x)
    news = dedupe(merged_news, 1200)

    # ---------- 공식자료 ----------
    # v4.3 이전 공식자료는 잘못된 링크/날짜가 섞였으므로 처음 한 번은 폐기 후 90일 재구축.
    prior_backfill_ok = bool(old.get('official_backfill_complete')) and old.get('official_backfill_version') == '4.3'
    backfill = not prior_backfill_ok

    fsc_items, official_status = collect_fsc_official(backfill, cutoff, now_utc)
    try:
        fss_items = collect_fss_incremental()
        official_status.append({'feed':'금융감독원', 'ok':True, 'count':len(fss_items), 'method':'exact_domain_search'})
    except Exception as e:
        fss_items = []
        official_status.append({'feed':'금융감독원', 'ok':False, 'count':0, 'error':str(e)[:160]})

    if backfill:
        # 오래된/잘못된 v4.2 공식자료는 사용하지 않는다.
        official_seed = []
    else:
        official_seed = existing_official

    official_merged = []
    for x in fsc_items + fss_items + official_seed:
        d = parse_dt(x.get('date'))
        if not d or d < cutoff:
            continue
        official_merged.append(x)
    official = dedupe(official_merged, 500)

    payload = {
        'updated_at': now_utc.isoformat(),
        'retention_days': RETENTION_DAYS,
        'feed_status': status,
        'official_status': official_status,
        'official_backfill_complete': True,
        'official_backfill_version': '4.3',
        'official_collection_mode': '90d_backfill' if backfill else '7d_incremental',
        'count': len(news),
        'official_count': len(official),
        'items': news,
        'official_items': official,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('DONE news=', len(news), 'official=', len(official), 'mode=', payload['official_collection_mode'])

if __name__ == '__main__':
    main()
