#!/usr/bin/env python3
import urllib.parse, urllib.request, xml.etree.ElementTree as ET
import json, re, html, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from difflib import SequenceMatcher

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
    ("불법사금융", '"불법사금융" OR "불법사채" OR "고리사채" OR "불법대부"'),
    ("투자사기", '("투자사기" OR "리딩방" OR "로맨스스캠" OR "코인사기") (자금 OR 계좌 OR 가상자산)'),
    ("환치기·외환", '"환치기" OR "불법외환" OR "외국환거래법 위반" OR "불법 송금"'),
    ("마약자금", '(마약 OR 필로폰 OR 대마) (자금 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("불법도박", '("불법도박" OR "온라인도박" OR "도박사이트") (자금 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("탈세", '(탈세 OR "조세포탈" OR "역외탈세") (차명 OR 계좌 OR 가상자산 OR 범죄수익)'),
    ("횡령·배임", '(횡령 OR 배임) (공금 OR 회삿돈 OR 회사자금 OR 법인자금 OR 자금 OR 계좌 OR 은닉 OR 유용 OR 빼돌 OR 고발 OR 구속 OR 기소)'),
    ("차명계좌", '"차명계좌" OR "차명 거래" OR "명의대여"'),
    ("제재·테러자금", '"테러자금" OR "제재 회피" OR "대북제재" OR "북한 가상자산"'),
]

# 5.5: 직원들이 실제로 공유하는 'AML 실무정보'를 놓치지 않기 위한 별도 발견 경로.
# 사건 기사와 달리 금융회사 운영·신종수법·차단체계·제도 변화도 후보로 수집한다.
PRACTICAL_QUERIES = [
    ("AML 운영·시스템", '(AML OR "자금세탁방지" OR STR OR "의심거래") (FDS OR AI OR 시스템 OR 자동화 OR 연계 OR 모니터링 OR "위험평가" OR 보고서)'),
    ("정보공유·자금차단", '(보이스피싱 OR 신종피싱 OR 범죄자금 OR 금융사기) ("정보공유" OR "지급정지" OR "거래정지" OR "신속차단" OR "의심계좌")'),
    ("AML 제도변화", '(FIU OR "금융정보분석원" OR 특금법 OR "자금세탁방지") (개정 OR 시행 OR 공유 OR 제재 OR 검사 OR 평가 OR 지침 OR 제도)'),
    ("가상자산 AML 실무", '(가상자산 OR VASP OR "해외거래소" OR "해외 거래소") (미신고 OR 트래블룰 OR 외부이전 OR 실태조사 OR 제도이행평가 OR 자금세탁 OR "불법 영업")'),
    ("PG·가상계좌", '(PG OR "가상계좌" OR 전자금융) (보이스피싱 OR 불법도박 OR 자금세탁 OR 재판매 OR 사기 OR 범죄자금)'),
    ("불법외환·관세", '(관세청 OR 외국환 OR 불법외환 OR 재산도피) (가상자산 OR 환치기 OR 불법송금 OR 범죄자금 OR 자금세탁)'),
    ("신종수법·취약점", '(상품권 OR 외화계좌 OR 스테이블코인 OR DEX OR eSIM OR "휴대폰 렌탈") (자금세탁 OR 보이스피싱 OR 신종피싱 OR 불법사금융 OR 사기)'),
    ("명의도용·차단", '("사망자 명의" OR 명의도용) (금융거래 OR 계좌 OR 지급정지 OR 거래정지 OR 불법)'),
    # 5.7: 실제 직원 공유 샘플에서 확인된 누락 유형을 좁은 조합으로 보강
    ("미신고 VASP·불법영업", '("미신고" OR "무등록") (VASP OR "가상자산사업자" OR "코인거래소" OR "가상자산 거래소" OR "해외거래소") (적발 OR 수사 OR "국내 영업" OR 제재 OR 차단)'),
    ("FIU·STR 정보공유", '(FIU OR "금융정보분석원" OR "의심거래") ("정보 공유" OR 정보공유 OR 은행 OR 금융회사) (특금법 OR 개정 OR 자금세탁 OR 추적 OR 제도)'),
    ("상품권·DEX 현금화", '(상품권 OR "상품권 깡") (스테이블코인 OR USDT OR JPYC OR DEX OR 현금화 OR 자금세탁 OR 사기)'),
    ("외화계좌·피싱 우회", '(외화계좌 OR "외화 계좌") (보이스피싱 OR 피싱 OR 자금세탁 OR 지급정지 OR 우회)'),
    # 5.7.2 회귀검증용: 이전에 실제로 놓친 대표 기사 제목/핵심구문을 90일 백필에서 직접 재탐색.
    ("회귀검증·미신고거래소", '"미신고 불법 코인거래소"'),
    ("회귀검증·FIU정보공유", 'FIU "자금세탁 의심거래" "은행"'),
    ("회귀검증·상품권깡", '스테이블코인 "상품권 깡"'),
    ("회귀검증·외화계좌", '보이스피싱 "외화계좌" 자금세탁'),
    # 5.8: PG/해외결제/계정도용 등 "자금세탁" 단어가 제목에 없거나 표현이 다른 실무형 기사 보강
    ("PG·결제대행 AML", '(PG OR "PG사" OR 결제대행 OR "결제 대행") (자금세탁 OR 자금세탁방지 OR AML OR CDD OR KYC OR 고객확인 OR 현금화 OR "세탁 통로" OR 보이스피싱 OR 사기)'),
    ("해외결제 AML", '(해외결제 OR "해외 결제") (자금세탁 OR AML OR CDD OR KYC OR 고객확인 OR "경영 유의" OR 관리부실 OR 검사 OR 제재)'),
    ("계정도용·현금화", '(계정도용 OR "계정 도용" OR 쇼핑몰) (결제 OR 현금화 OR 자금세탁 OR "세탁 통로" OR 범죄자금)'),
    ("외화·상품권 우회", '(외화계좌 OR "외화 계좌" OR 상품권) (보이스피싱 OR 신종피싱 OR 자금세탁 OR 현금화 OR 지급정지 OR 우회)'),
]

# 금융위/FIU 공식 보도자료 직접검색용 키워드.
# 너무 잘게 쪼개면 요청 수가 폭증하므로, 실무적으로 빠짐을 줄이면서도 10개로 제한.
# 5.8: 일반 검색에서 상위 100건 밖으로 밀리거나 색인이 늦는 경우를 보완하기 위한
# 출처 집중 검색. 별도 API를 쓰지 않고 Google News RSS를 좁은 site: 질의로 한 번 더 확인한다.
SOURCE_FOCUSED_DOMAINS = [
    'asiae.co.kr','edaily.co.kr','segye.com','heraldcorp.com','newspim.com','raonnews.com'
]
SOURCE_FOCUSED_QUERY = '(자금세탁 OR 자금세탁방지 OR AML OR FIU OR 특금법 OR 보이스피싱 OR 외화계좌 OR 상품권 OR CDD OR KYC OR 고객확인 OR PG OR 결제대행 OR 미신고 OR VASP)'

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
    "신종피싱",
    "가상계좌",
    "사망자 명의",
]

# 검색 결과를 다시 거르는 AML 관련어. 제목에 하나 이상 있어야 공식자료로 채택.
OFFICIAL_KEEP = [
    # 공식자료는 "일반 금융정책"이 아니라 AML/금융범죄 실무에 직접 연결되는 표현만 유지한다.
    "금융정보분석원","fiu","자금세탁","자금세탁방지","aml","cft","fatf",
    "의심거래","str","고액현금거래","ctr","특정금융정보법","특금법",
    "가상자산사업자","vasp","트래블룰","고객확인","kyc","제도이행평가",
    "테러자금","보이스피싱","신종피싱","대포통장","불법사금융","불법금융",
    "환치기","미신고 해외","미신고 가상자산","미등록 가상자산","미신고 거래소",
    "금융사기","지급정지","거래정지","신속차단","정보공유","사기이용계좌",
    "fds","사망자 명의","불법외환","재산도피"
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
FSC_RSS_URL = "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111"
FSS_QUERY = '("자금세탁" OR AML OR CFT OR FIU OR "보이스피싱" OR "대포통장" OR "가상자산" OR "불법금융" OR "자금세탁방지") site:fss.or.kr'

def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 K-AML-News/5.8.0'})
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
    if re.search(r'불법사금융|불법사채|고리사채|불법대부|불법추심', t): tags.append('불법사금융')
    if re.search(r'보이스피싱|신종피싱|금융사기|사기|리딩방|로맨스스캠|대포통장', t): tags.append('사기')
    if re.search(r'마약|필로폰|대마', t): tags.append('마약')
    if re.search(r'도박|카지노|베팅', t): tags.append('도박')
    if re.search(r'탈세|조세포탈|역외탈세', t): tags.append('탈세')
    if re.search(r'횡령|배임', t): tags.append('횡령·배임')
    if re.search(r'환치기|불법외환|외국환', t): tags.append('환치기·외환')
    if re.search(r'fiu|금융정보분석원|str|의심거래|자금세탁방지|규제|제도|fatf|fds|지급정지|거래정지|신속차단|정보공유|가상계좌|실태조사', t): tags.append('FIU·규제')
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
    'asiae.co.kr','segye.com','segye.co.kr','heraldcorp.com','bizwatch.co.kr','etoday.co.kr','ajunews.com',
    'newsway.co.kr','thebell.co.kr','dealsite.co.kr','newspim.com','raonnews.com',
    # IT/가상자산 전문 매체 중 기사형 출처
    'zdnet.co.kr','etnews.com','digitaltoday.co.kr','ddaily.co.kr','bloter.net',
    'tokenpost.kr','blockmedia.co.kr','decenter.kr',
    # 지역/기타 주요 언론
    'busan.com','imaeil.com','kwnews.co.kr','kado.net','jnilbo.com','jjan.kr','jejunews.com'
]

TRUSTED_SOURCE_NAMES = [
    '연합뉴스','뉴시스','뉴스1','kbs','mbc','sbs','ytn','jtbc','mbn','tv조선','채널a',
    '조선일보','중앙일보','동아일보','한겨레','경향신문','한국일보','매일경제','한국경제',
    '서울경제','세계일보','파이낸셜뉴스','머니투데이','이데일리','아시아경제','헤럴드경제',
    '비즈워치','이투데이','아주경제','뉴스웨이','더벨','딜사이트','뉴스핌','라온신문',
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

    # 프리스핀/가입혜택 등 카지노 SEO 문구는 '자금세탁' 같은 AML 단어를 끼워 넣어도 기사로 보지 않는다.
    casino_seo_terms = [
        '프리스핀','무료스핀','가입보너스','가입 보너스','첫충전','첫 충전',
        '재충전','충전보너스','충전 보너스','가입코드','가입 코드','추천코드','추천 코드',
        '카지노사이트','카지노 사이트','슬롯사이트','슬롯 사이트','바카라사이트','바카라 사이트'
    ]
    concrete_enforcement = [
        '경찰','검찰','법원','금감원','금융위','국세청','관세청',
        '검거','구속','기소','송치','적발','압수','추징','수사','선고','징역','벌금'
    ]
    if any(tok in t for tok in casino_seo_terms) and not any(sig in t for sig in concrete_enforcement):
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


# ---------- v4.8 국내뉴스 기사성/카테고리 판정 ----------
# Google 검색피드는 후보 발견에만 사용한다.
# 실제 게재 여부와 표시 카테고리는 기사 제목에서 다시 판정한다.

V48_NONARTICLE_HOST_PREFIXES = ['stock.','search.','m.stock.','finance.']
V48_NONARTICLE_PATH_TOKENS = ['/search','/board','/bbs','/community','/user','/profile']
V48_SOLICITATION_TERMS = [
    '팝니다','삽니다','판매합니다','구매합니다','가입문의','가입 문의','문의주세요',
    '추천인 코드','추천코드','가입 코드','첫충전','재충전','롤링','페이백',
    '무료머니','보너스 지급','충전 이벤트','가입 이벤트'
]
V48_CONTACT_TERMS = ['텔레그램','오픈채팅','카톡 문의','카카오톡 문의']

def v48_nonarticle(title, source_url, source_domain):
    t = title or ''
    d = (source_domain or '').lower()
    u = (source_url or '').lower()
    if any(d.startswith(p) for p in V48_NONARTICLE_HOST_PREFIXES):
        return True
    try:
        path = urllib.parse.urlparse(u).path.lower()
    except Exception:
        path = ''
    if any(tok in path for tok in V48_NONARTICLE_PATH_TOKENS):
        return True
    if re.search(r'@[A-Za-z0-9_]{4,}', t):
        return True
    if any(tok in t for tok in V48_SOLICITATION_TERMS):
        return True
    if any(c in t for c in V48_CONTACT_TERMS) and re.search(r'(문의|판매|구매|가입|코드|연락)', t):
        return True
    return False

def infer_news_category(title):
    t = (title or '').lower()

    # 직접 AML / 범죄수익
    if re.search(r'자금세탁|돈세탁|세탁한|세탁해|세탁\s*혐의|세탁\s*통로|세탁\s*경로|세탁\s*창구|money laundering', t):
        return '자금세탁'
    if re.search(r'범죄수익|범죄 수익|범죄수익은닉|범죄수익 환수|범죄수익환수|몰수|추징', t):
        return '범죄수익'

    # FIU / STR / AML 제도
    if re.search(r'금융정보분석원|\bfiu\b|의심거래보고|의심거래|고액현금거래|\bstr\b|\bctr\b|자금세탁방지|\baml\b|\bcft\b|\bfatf\b|고객확인|\bkyc\b|제도이행평가', t):
        return 'FIU·STR'

    # 가상자산 규제 / 트래블룰
    if '트래블룰' in t:
        return '트래블룰'
    if re.search(r'가상자산사업자|\bvasp\b', t) and re.search(r'신고|미등록|등록|특금법|특정금융정보법|규제|제재|검사|점검|위반|의무|매뉴얼', t):
        return '트래블룰'

    # 주요 전제범죄 / 사기
    if re.search(r'보이스피싱|신종피싱|대포통장|사기이용계좌|전화금융사기', t):
        return '보이스피싱'
    if re.search(r'불법사금융|불법사채|고리사채|불법대부|초고금리\s*대출|불법추심', t):
        return '불법사금융'
    if re.search(r'투자사기|투자 사기|리딩방|로맨스스캠|로맨스 스캠|코인사기|코인 사기|유사수신', t):
        return '투자사기'
    if re.search(r'환치기|불법\s*외환|불법\s*외화|외국환거래법\s*위반|불법\s*송금|무등록\s*외환|외환거래\s*적발', t):
        return '환치기·외환'

    drug = re.search(r'마약|필로폰|대마|코카인|마약류', t)
    drug_money = re.search(r'자금|대금|계좌|가상자산|코인|범죄수익|송금|입금|출금|세탁|추징|몰수|수익', t)
    if drug and drug_money:
        return '마약자금'

    gamble = re.search(r'불법도박|온라인도박|도박사이트|도박 사이트|불법\s*카지노|사설토토|사설\s*토토', t)
    gamble_case = re.search(r'검거|적발|구속|기소|송치|수사|운영|조직|일당|범죄|자금|계좌|수익|세탁|추징|압수|피해', t)
    if gamble and gamble_case:
        return '불법도박'

    if re.search(r'탈세|조세포탈|역외탈세|세금\s*포탈|세금\s*탈루', t):
        return '탈세'

    if re.search(r'횡령|배임|업무상횡령|업무상배임|회삿돈.{0,8}(빼돌|유용)|회사자금.{0,8}(빼돌|유용)|법인자금.{0,8}(빼돌|유용)', t):
        return '횡령·배임'

    if re.search(r'차명계좌|차명\s*계좌|차명\s*거래|명의대여|명의\s*대여', t):
        return '차명계좌'

    if re.search(r'테러자금|테러\s*자금|제재\s*회피|대북제재|대북\s*제재|북한.{0,15}(가상자산|암호화폐|코인|해킹|자금)|제재위반|제재\s*위반', t):
        return '제재·테러자금'

    # 가상자산 일반 시장/투자/기술 뉴스는 제외하고 범죄·불법·AML 맥락이 있어야 함.
    crypto = re.search(r'가상자산|암호화폐|비트코인|이더리움|\busdt\b|테더|코인|가상화폐', t)
    risk = re.search(r'자금세탁|범죄|불법|사기|피싱|환치기|탈취|해킹|랜섬웨어|제재|수사|검거|적발|구속|기소|송치|피해|미등록|특금법|특정금융정보법', t)
    if crypto and risk:
        return '가상자산 AML'

    # 개인지갑/해외거래소는 실제 자금이동·규제·범죄 맥락이 제목에 있어야 함.
    wallet = re.search(r'개인지갑|개인\s*지갑|해외거래소|해외\s*거래소|외부지갑|외부\s*지갑|가상자산\s*지갑|암호화폐\s*지갑|지갑', t)
    wallet_context = re.search(r'송금|이체|입금|출금|이동|전송|자금|거래|거래소|추적|동결|압수|제재|수사|범죄|불법|신고|규제|차단|현금화', t)
    if wallet and wallet_context:
        return '지갑·해외거래소'

    return None


# ---------- v4.9 AML 실무가치 최종 게이트 ----------
# 제목에서 "금융범죄/AML 주제"뿐 아니라 실제 자금흐름·수법·집행·제도 변화가 보여야 한다.
# 애매하면 제외하는 정밀도 우선(precision-first) 방식.

V49_DIRECT_AML = re.compile(
    r'자금세탁|돈세탁|세탁\s*혐의|범죄수익|금융정보분석원|\bfiu\b|의심거래|의심거래보고|\bstr\b|'
    r'고액현금거래|\bctr\b|자금세탁방지|\baml\b|\bcft\b|\bfatf\b|특정금융정보법|특금법|'
    r'트래블룰|가상자산사업자|\bvasp\b|고객확인|\bkyc\b|테러자금|제재\s*회피'
)
V49_MONEY_FLOW = re.compile(
    r'계좌|대포통장|차명|송금|이체|입금|출금|현금|환치기|외환|외화|자금|대금|수익|'
    r'범죄수익|가상자산|암호화폐|코인|\busdt\b|테더|지갑|거래소|ATM|현금화|상품권|'
    r'몰수|추징|압수|동결|환수|빼돌|유용|은닉|세탁|공금|회삿돈|회사자금|법인자금|허위급여|비자금'
)
V49_CASE_ACTION = re.compile(
    r'적발|검거|구속|기소|송치|수사|압수|추징|몰수|동결|환수|징역|실형|벌금|'
    r'유죄|선고|피해|조직|일당|주범|총책|범행|사기|불법|위반|탈취|해킹|체포|고발|입건|혐의\s*확인'
)
V49_PREDICATE = re.compile(
    r'보이스피싱|신종피싱|전화금융사기|리딩방|투자사기|투자\s*사기|유사수신|로맨스\s*스캠|'
    r'불법도박|온라인도박|도박사이트|사설토토|마약|필로폰|대마|코카인|'
    r'탈세|조세포탈|횡령|배임|불법\s*외환|외국환거래법|환치기|대포통장|차명계좌|불법사금융|불법사채|고리사채|불법대부'
)
V53_CONCRETE_AMOUNT = re.compile(
    r'(?:약\s*)?\d[\d,.]*(?:억|만|천)?\s*(?:원|달러|페소|유로)|'
    r'\d[\d,.]*\s*(?:억원|만원|달러|페소|유로)'
)

V49_POLICY_ACTION = re.compile(
    r'개정|시행|의결|입법|법안|규정|가이드|매뉴얼|지침|제재|검사|점검|평가|'
    r'신고제|등록|미등록|의무|금지|강화|개선|대책|조치'
)
V49_LOW_VALUE = re.compile(
    r'금융당국\s*일정|주간\s*일정|다음주.*일정|증시|주가|시황|전망|목표주가|'
    r'토큰증권|STO|ETF|상장\s*예정|신제품|출시|파트너십|협약|MOU|교육\s*프로그램|'
    r'세미나|컨퍼런스|포럼|캠페인|홍보|무료\s*보험|감사장|수상|이벤트'
)
V49_FOREIGN_POLITICS = re.compile(
    r'대선|총선|지지율|대통령|총리|의회|정권|후보|선거'
)

def v49_practical_aml(title):
    t = title or ''

    # 직접 AML/규제 제목은 집행 또는 제도변화가 있으면 유지.
    direct = bool(V49_DIRECT_AML.search(t))
    flow = bool(V49_MONEY_FLOW.search(t))
    action = bool(V49_CASE_ACTION.search(t))
    predicate = bool(V49_PREDICATE.search(t))
    policy = bool(V49_POLICY_ACTION.search(t))

    # 일반 일정/시장/산업/홍보 기사는 직접 AML 사건·제도 신호가 없는 한 제외.
    if V49_LOW_VALUE.search(t) and not (direct and (action or policy)):
        return False

    # 해외 일반 정치/선거 비리: 직접 AML 또는 명확한 자금흐름+금융범죄가 아니면 제외.
    if V49_FOREIGN_POLITICS.search(t) and not direct and not (predicate and flow):
        return False

    # 직접 AML: 제목 자체로 충분히 유의미하거나, 제도/집행 변화가 확인되면 유지.
    if direct and (action or policy or flow):
        return True

    # 전제범죄는 "범죄명만"으로 부족. 자금흐름/금융거래 또는 강한 사건 집행 맥락 필요.
    if predicate and flow:
        return True

    # 횡령·배임·탈세 등도 구체적 금액 + 수사/고발/구속/기소 등 사건성이 있으면 실무 사례로 유지.
    if predicate and action and V53_CONCRETE_AMOUNT.search(t):
        return True

    # 보이스피싱/투자사기/불법도박은 새로운 사건·수법을 놓치지 않도록
    # 구체적인 수사/피해/조직/형사처분 신호가 있으면 유지.
    if predicate and action and re.search(r'보이스피싱|신종피싱|전화금융사기|리딩방|투자사기|투자\s*사기|유사수신|불법도박|온라인도박|도박사이트|사설토토', t):
        return True

    # 가상자산은 단순 시장/기술이 아니라 범죄·불법·집행이 결합되어야 함.
    if re.search(r'지갑', t) and re.search(r'거래소', t) and re.search(r'현금화|송금|이체|전송|동결|압수|추적', t):
        return True

    crypto = re.search(r'가상자산|암호화폐|비트코인|이더리움|\busdt\b|테더|코인|가상화폐', t)
    crypto_risk = re.search(r'자금세탁|범죄|불법|사기|피싱|환치기|탈취|해킹|랜섬웨어|제재|미등록|위반', t)
    if crypto and crypto_risk and (action or flow):
        return True

    return False


# ---------- 5.8.1 precision guard ----------
# 발견 범위를 넓힌 5.8의 장점은 유지하되, 제목만 보고도 명백한 오탐인 경우만 좁게 제거한다.
def v581_obvious_noise(title):
    t = title or ''
    low = t.lower()

    # 5.8.3: '상품권'이라는 단어 자체는 AML 신호가 아니다.
    # 기탁·증정·행사·일반 위조/사기 사건 등은 제외하고, 실제 자금세탁/현금화/우회
    # 또는 금융통제 취약점과 연결된 경우에만 상품권 기사를 유지한다.
    if re.search(r'상품권', t):
        giftcard_aml_risk = re.search(
            r'자금세탁|돈세탁|세탁\s*통로|세탁\s*경로|범죄수익|보이스피싱|신종피싱|'
            r'대포통장|외화계좌|현금화|상품권\s*깡|\bDEX\b|\bUSDT\b|JPYC|KRWQ|'
            r'환전|(?<!통)우회|지급정지|거래정지|계좌|송금|자금\s*흐름|가상자산|스테이블코인|'
            r'미신고|무등록|\bVASP\b|FIU|의심거래|\bSTR\b',
            t, re.I
        )
        if not giftcard_aml_risk:
            return True
    # 상품권이 등장해도 살인/강력범죄의 동기·은폐 수단일 뿐 AML/금융범죄 흐름이 아니면 제외.
    # 상품권이 살인·사망 사건의 동기/수사 단서로 등장한 일반 형사사건은 제외.
    # '현금화/사기'라는 단어만 있어도 살려버리던 5.8.1의 빈틈을 막되,
    # 자금세탁·범죄수익·보이스피싱·계좌흐름처럼 AML 연결이 명시된 경우는 유지한다.
    violent_giftcard = (
        re.search(r'상품권', t)
        and (
            re.search(r'살인|살해|피살|사망|숨진|숨져|시신|변사|흉기|납치|강도살인|냉동창고|냉동\s*컨테이너', t)
            or (re.search(r'파주', t) and re.search(r'카페|냉동|상품권\s*사기|위조\s*상품권|계획범죄', t))
        )
    )
    if violent_giftcard:
        if not re.search(r'자금세탁|돈세탁|범죄수익|보이스피싱|신종피싱|대포통장|외화계좌|FIU|의심거래|\bSTR\b', t, re.I):
            return True
    # 포상/캠페인/예방주간 자체가 중심인 홍보성 기사. 단, 실제 통제·시스템 변화가 제목에 있으면 유지.
    if re.search(r'포상|예방\s*주간|예방\s*캠페인|홍보대사|예방\s*교육', t):
        if not re.search(r'지급정지|거래정지|정보공유|시스템|플랫폼|가이드라인|지침|제도|개정|의무|차단|동결|신속대응|AI|인공지능', t, re.I):
            return True
    # 단순 개인 피해담/주의 환기형 보이스피싱 기사. 수법·자금흐름·통제 변화가 있으면 유지.
    if re.search(r'하마터면|당할\s*뻔|피해\s*예방|주의하세요|조심하세요', t) and re.search(r'보이스피싱|피싱', t):
        if not re.search(r'대포통장|외화계좌|상품권|가상자산|코인|계좌|송금|현금화|지급정지|수법|조직|검거|적발|차단|시스템', t):
            return True
    return False

# ---------- 5.5 AML 실무정보 통과 경로 ----------
# 범죄 사건만 찾는 기존 v4.9 게이트는 그대로 유지하고,
# 금융회사 AML 운영·제도변화·신종수법·차단체계 같은 업무 참고정보를 별도로 살린다.
V55_AML_OPS = re.compile(r'aml|자금세탁방지|자금세탁|의심거래|\bstr\b|\bfds\b|금융정보분석원|\bfiu\b|\bcdd\b|\bkyc\b|고객확인')
V55_OPS_CHANGE = re.compile(r'시스템|ai|인공지능|자동화|연계|모니터링|위험평가|보고서|정보공유|공유|지급정지|거래정지|신속차단|차단|동결|통합|플랫폼|가이드라인|내부통제|관리\s*부실|경영\s*유의|개선')
V55_POLICY = re.compile(r'개정|시행|법안|규정|기준|지침|제도|의결|신고|미신고|검사|점검|평가|제재|과태료|영업정지|실태조사')
V55_FINANCIAL_ORG = re.compile(r'은행|금융회사|금융권|금융당국|금융위|금감원|금융감독원|fiu|금융정보분석원|거래소|가상자산사업자|vasp|pg사|pg\b|결제대행|전자금융|간편결제|해외결제')
V55_CONTROL_RISK = re.compile(r'보이스피싱|신종피싱|금융사기|불법사금융|불법도박|마약|범죄자금|불법재산|사망자\s*명의|명의도용|가상계좌')
V55_CRYPTO_CONTROL = re.compile(r'가상자산|암호화폐|코인|usdt|테더|해외\s*거래소|해외거래소|지갑|트래블룰|vasp|가상자산사업자')
V55_CRYPTO_PRACTICE = re.compile(r'미신고|무등록|불법\s*영업|국내\s*영업|수사의뢰|적발|외부이전|외부\s*이전|실태조사|제도이행평가|신고|영업정지|제재|외환\s*전산망|유출입|현금화|차단')
V55_NEW_TYPOLOGY = re.compile(r'상품권|외화계좌|스테이블코인|dex|eSIM|휴대폰\s*렌탈|가상계좌|재판매|결제대행|pg사|pg\b|해외결제|계정\s*도용|계정도용|쇼핑몰')
V55_TYPOLOGY_RISK = re.compile(r'자금세탁|돈세탁|세탁\s*통로|세탁\s*경로|보이스피싱|신종피싱|사기|불법사금융|불법도박|범죄|사각지대|우회|악용|현금화|지급정지|관리\s*부실|경영\s*유의|\bcdd\b|\bkyc\b|고객확인')
V573_CRYPTO_GIFTCARD = re.compile(r'(상품권|상품권\s*깡).{0,60}(스테이블코인|usdt|jpyc|krwq|dex)|(스테이블코인|usdt|jpyc|krwq|dex).{0,60}(상품권|상품권\s*깡)', re.I)
V573_GIFTCARD_RISK = re.compile(r'깡|현금화|규제|미신고|무등록|vasp|불법|사각지대|우회|자금세탁', re.I)
V55_FX = re.compile(r'관세청|불법외환|불법\s*외환|외국환|재산도피|불법송금|환치기')

def v55_practical_info(text):
    t = text or ''
    low = t.lower()
    # 일반 산업/시장/행사성 글은 여전히 제외
    if V49_LOW_VALUE.search(t) and not (V55_AML_OPS.search(low) and (V55_OPS_CHANGE.search(low) or V55_POLICY.search(low))):
        return False
    # 금융회사/당국의 AML·FDS·STR 운영 변화
    if V55_FINANCIAL_ORG.search(low) and V55_AML_OPS.search(low) and (V55_OPS_CHANGE.search(low) or V55_POLICY.search(low)):
        return True
    # 범죄자금/사기 계좌의 정보공유·지급정지·신속차단 체계
    if V55_CONTROL_RISK.search(low) and (V55_OPS_CHANGE.search(low) or V55_POLICY.search(low)):
        return True
    # 가상자산사업자·해외거래소의 AML 통제/취약점/외부이전 변화
    if V55_CRYPTO_CONTROL.search(low) and V55_CRYPTO_PRACTICE.search(low):
        return True
    # 상품권·외화계좌·DEX·eSIM·가상계좌 등 신종 수법/사각지대
    if V55_NEW_TYPOLOGY.search(low) and V55_TYPOLOGY_RISK.search(low):
        return True
    # 5.7.3 회귀검증: '스테이블코인/DEX + 상품권 깡'처럼 제목에
    # 자금세탁이라는 단어가 없어도 현금화·규제회피 위험이 명확한 조합은 유지한다.
    if V573_CRYPTO_GIFTCARD.search(low) and V573_GIFTCARD_RISK.search(low):
        return True
    # 관세청/외국환 영역의 자금흐름·범죄 통제
    if V55_FX.search(low) and (V49_MONEY_FLOW.search(low) or V49_CASE_ACTION.search(low) or V55_POLICY.search(low)):
        return True
    return False

def v56_practical_category(text):
    """5.6: 'AML 실무·제도'라는 별도 카테고리 없이 기존 업무 카테고리에 배치한다."""
    t = text or ''
    low = t.lower()
    # 특정 범죄/수법이 명확하면 그 카테고리를 우선한다.
    if re.search(r'보이스피싱|신종피싱|전화금융사기|대포통장|사기이용계좌', t):
        return '보이스피싱'
    if re.search(r'불법사금융|불법사채|고리사채|불법대부', t):
        return '불법사금융'
    if re.search(r'환치기|불법\s*외환|외국환|재산도피|불법송금', t):
        return '환치기·외환'
    if re.search(r'불법도박|도박사이트|온라인도박', t):
        return '불법도박'
    if re.search(r'마약|필로폰|코카인|대마', t):
        return '마약자금'
    if re.search(r'횡령|배임|회삿돈|회사자금|법인자금|공금', t):
        return '횡령·배임'
    if re.search(r'탈세|조세포탈|역외탈세|탈루', t):
        return '탈세'
    if re.search(r'테러자금|제재\s*회피|대북제재|제재위반', t):
        return '제재·테러자금'
    # 스테이블코인/DEX를 이용한 상품권 현금화·규제회피는 가상자산 AML로 분류.
    if V573_CRYPTO_GIFTCARD.search(low) and V573_GIFTCARD_RISK.search(low):
        return '가상자산 AML'
    # 가상자산 제도/사업자/해외거래소 실무정보는 가상자산 쪽으로.
    if V55_CRYPTO_CONTROL.search(low):
        if re.search(r'트래블룰|vasp|가상자산사업자|미신고|신고|특금법|특정금융정보법', low):
            return '트래블룰'
        return '가상자산 AML'
    # STR/FIU/AML 시스템, 정보공유, 지급정지·거래차단 등은 FIU·STR로 묶는다.
    if V55_AML_OPS.search(low) or re.search(r'사망자\s*명의|지급정지|거래정지|신속차단|정보공유|fds', low):
        return 'FIU·STR'
    # PG/가상계좌 등 통제정보는 범죄 맥락이 없으면 FIU·STR이 가장 가까운 업무 분류.
    if re.search(r'pg사|가상계좌|전자금융', low):
        return 'FIU·STR'
    return 'FIU·STR'

def strong_unlisted_source_ok(title, context, source_name, domain):
    """5.8: 화이트리스트에 아직 없는 언론도 강한 AML/금융범죄 신호가 있으면 제한적으로 허용.
    광고·카지노·리퍼럴 출처는 기존 차단 토큰으로 먼저 제외한다.
    """
    t = title or ''
    c = context or t
    d = (domain or '').lower()
    sname = (source_name or '').lower()
    if not d and not sname:
        return False
    if any(tok in d for tok in BLOCKED_DOMAIN_TOKENS) or any(tok in sname for tok in BLOCKED_SOURCE_TOKENS):
        return False
    if len(t.strip()) < 10:
        return False
    # 제목 자체가 직접 AML/FIU/특금법/자금세탁을 말하면 가장 강하게 허용.
    if V49_DIRECT_AML.search(t) and (V49_CASE_ACTION.search(t) or V49_POLICY_ACTION.search(t) or V49_MONEY_FLOW.search(t) or V55_OPS_CHANGE.search(t)):
        return True
    # 새로운 수법·통제 취약점은 제목+설명 맥락까지 보되, 실무가치 판정까지 통과해야 한다.
    if v55_practical_info(c) and re.search(
        r'보이스피싱|신종피싱|외화계좌|상품권|결제대행|pg사|\bpg\b|해외결제|계정\s*도용|'
        r'자금세탁|세탁\s*통로|\bcdd\b|\bkyc\b|고객확인|특금법|금융정보분석원|\bfiu\b|'
        r'미신고|무등록|\bvasp\b', c, re.I):
        return True
    return False

def fetch_query(name, query, window_days=1):
    # 기본 뉴스는 최근 24시간, 5.5 실무정보는 최초 1회 90일 백필 가능
    params = urllib.parse.urlencode({
        'q': query + f' when:{int(window_days)}d',
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
        description = strip_html(it.findtext('description') or '')
        context = (title + ' ' + description).strip()
        # 5.8: 고정 화이트리스트만으로는 뉴스핌·라온신문처럼 유효한 매체가 빠질 수 있다.
        # 알려진 신뢰매체는 그대로 통과시키고, 미등록 매체는 "강한 AML 실무 신호"가 있을 때만 예외 허용한다.
        if not (trusted_news_source(source_name, source_domain) or strong_unlisted_source_ok(title, context, source_name, source_domain)):
            continue
        if v48_nonarticle(title, source_url, source_domain):
            continue
        if low_quality_news_source(title, source_name, source_domain):
            continue
        category = infer_news_category(title)
        if not category:
            category = infer_news_category(context)
        if not category and (v55_practical_info(title) or v55_practical_info(context)):
            category = v56_practical_category(context)
        if not category:
            continue
        # 제목만으로 충분하면 그대로 통과. 제목이 짧거나 맥락이 부족한 경우
        # Google News 설명문까지 보조적으로 보되, 실무가치 게이트는 동일하게 적용한다.
        if not (v49_practical_aml(title) or v49_practical_aml(context) or v55_practical_info(title) or v55_practical_info(context)):
            continue
        out.append({
            'region': '국내',
            'query': category,
            'title': title,
            'source': source_name,
            'source_url': source_url,
            'source_domain': source_domain,
            'date': it.findtext('pubDate') or '',
            'link': it.findtext('link') or '',
            'tags': classify(title),
            'description': description[:700],
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

def parse_fsc_board_page(raw_html):
    """금융위 보도자료 목록에서 상세페이지 링크/제목/날짜를 직접 추출한다.
    검색 키워드에 의존하지 않아 제목이 평범해도 본문이 FIU/AML이면 놓치지 않는다.
    """
    rows = []
    pat = re.compile(r'<a\b[^>]*href=["\']([^"\']*/no010101/\d+[^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
    matches = list(pat.finditer(raw_html))
    seen = set()
    for i, m in enumerate(matches):
        href = html_unescape(m.group(1))
        title = clean_title(strip_html(m.group(2)))
        # 금융위 목록의 접근성용 숨김 문구가 제목 안에 섞이는 경우 제거.
        # 예: "... 발표. 금일 등록된 게시글" -> 실제 상세페이지 제목만 남김.
        title = re.sub(r'[.\s]*(?:금일\s*등록된\s*게시글|새\s*글)\s*$', '', title, flags=re.I).strip()
        if not title or len(title) < 4:
            continue
        detail = urllib.parse.urljoin("https://www.fsc.go.kr", href).replace('&amp;', '&')
        detail = detail.split('#')[0]
        if detail in seen:
            continue
        seen.add(detail)
        next_pos = matches[i+1].start() if i+1 < len(matches) else min(len(raw_html), m.end()+3500)
        chunk = strip_html(raw_html[m.end():next_pos])
        dm = re.search(r'(20\d{2})[.\-/](\d{2})[.\-/](\d{2})', chunk)
        if not dm:
            continue
        date = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}T00:00:00+09:00"
        rows.append({'region':'공식자료','query':'금융위/FIU','official_source':'금융위원회','official_sources':['금융위원회'],
                     'title':title,'source':'금융위원회','date':date,'link':detail,
                     'tags':classify(title),'collection_method':'direct_fsc_board'})
    return rows

def collect_fsc_rss_candidates():
    """금융위원회 공식 보도자료 RSS를 목록 파싱의 보조 경로로 사용한다.
    게시판 HTML 구조가 바뀌어 최신 페이지 링크 추출이 실패해도 최신 보도자료를 회수한다.
    최종 채택 여부는 기존 verify_fsc_detail()에서 동일하게 검증한다.
    """
    data = fetch_url(FSC_RSS_URL, timeout=10)
    root = ET.fromstring(data)
    rows = []
    seen = set()
    for it in root.findall('.//item')[:100]:
        title = clean_title(strip_html(it.findtext('title') or ''))
        link = html_unescape((it.findtext('link') or it.findtext('guid') or '').strip()).replace('&amp;', '&')
        if not title or not link:
            continue
        # RSS가 상대경로를 주는 경우도 안전하게 금융위 절대주소로 변환한다.
        link = urllib.parse.urljoin('https://www.fsc.go.kr', link)
        # 실제 보도자료 상세페이지(/no010101/<번호>)만 후보로 사용한다.
        m = re.search(r'https?://(?:www\.)?fsc\.go\.kr/no010101/\d+(?:\?[^#\s]*)?', link, re.I)
        if not m:
            continue
        link = m.group(0)
        if link in seen:
            continue
        seen.add(link)
        dt = parse_dt(it.findtext('pubDate') or '')
        if not dt:
            # 일부 RSS에서 pubDate가 없을 경우 설명문에서 날짜를 보조 추출한다.
            blob = strip_html((it.findtext('description') or '') + ' ' + ET.tostring(it, encoding='unicode'))
            dm = re.search(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', blob)
            if dm:
                date = f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}T00:00:00+09:00"
            else:
                continue
        else:
            date = dt.isoformat()
        rows.append({
            'region':'공식자료','query':'금융위/FIU','official_source':'금융위원회','official_sources':['금융위원회'],
            'title':title,'source':'금융위원회','date':date,'link':link,
            'tags':classify(title),'collection_method':'fsc_official_rss'
        })
    return rows

def verify_fsc_detail(item):
    """상세페이지 본문까지 확인해 AML/FIU/금융범죄 실무 관련 자료만 채택한다."""
    try:
        raw = fetch_url(item['link'], timeout=8).decode('utf-8', errors='ignore')
        text = strip_html(raw)
        title_key = key_title(item.get('title', ''))
        text_key = key_title(text)
        if not title_key or title_key not in text_key or len(text) < 350:
            return None
        title = item.get('title','')
        context = (title + ' ' + text[:12000]).strip()
        # 5.7.2: 본문 어딘가의 '제재/점검/가상자산' 같은 일반어 하나 때문에
        # 금융위 전체 보도자료가 들어오지 않도록 제목을 우선 판정한다.
        title_hit = official_relevant(title) or v55_practical_info(title) or v49_practical_aml(title)
        body_narrow = bool(re.search(
            r'(금융정보분석원|\bFIU\b|자금세탁방지|의심거래|\bSTR\b|특정금융정보법|특금법|'
            r'보이스피싱|신종피싱|사기이용계좌|지급정지|사망자\s*명의|트래블룰|가상자산사업자|\bVASP\b)',
            context, re.I))
        if not (title_hit or body_narrow):
            return None
        x = dict(item)
        # 5.8.9: 금융위 게시판 자료는 출처를 금융위원회로 유지한다.
        # FIU 자료는 KoFIU 공식 게시판에서 별도로 수집한 뒤 같은 보도자료끼리 통합한다.
        x['official_source'] = '금융위원회'
        x['official_sources'] = ['금융위원회']
        x['source'] = '금융위원회'
        x['tags'] = classify(context)
        return x
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
        if kt:
            seen_t.add(kt)
        if lk:
            seen_l.add(lk)
        out.append(x)
    out.sort(key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True)
    return out[:limit]

def collect_fsc_official(backfill, cutoff, now_utc):
    """5.7: 키워드 검색 대신 금융위 보도자료 목록을 날짜순으로 직접 순회한다."""
    max_pages = 30 if backfill else 3
    candidates, status = [], []
    for page in range(1, max_pages + 1):
        try:
            url = FSC_BOARD_URL + '?' + urllib.parse.urlencode({'curPage': str(page)})
            raw = fetch_url(url, timeout=10).decode('utf-8', errors='ignore')
            rows = parse_fsc_board_page(raw)
            candidates.extend(rows)
            status.append({'feed':f'금융위/FIU:목록:p{page}','ok':True,'count':len(rows),'method':'direct_official_board'})
            dated=[parse_dt(x.get('date')) for x in rows if parse_dt(x.get('date'))]
            if dated and min(dated) < cutoff:
                break
        except Exception as e:
            status.append({'feed':f'금융위/FIU:목록:p{page}','ok':False,'count':0,'error':str(e)[:160]})
            break
        time.sleep(0.05)
    # 5.8.9.1: 금융위가 공식 제공하는 보도자료 RSS를 항상 보조 경로로 확인한다.
    # HTML 목록 파서가 최신 1페이지 구조 변경을 놓쳐도 RSS 후보를 합쳐 복구하며,
    # 아래 상세페이지 검증은 기존과 동일하므로 일반 금융정책 자료가 과수집되지 않는다.
    try:
        rss_rows = collect_fsc_rss_candidates()
        candidates.extend(rss_rows)
        status.append({'feed':'금융위/FIU:RSS','ok':True,'count':len(rss_rows),'method':'official_rss_fallback'})
    except Exception as e:
        status.append({'feed':'금융위/FIU:RSS','ok':False,'count':0,'error':str(e)[:160]})

    candidates=[x for x in dedupe(candidates,700) if parse_dt(x.get('date')) and parse_dt(x.get('date'))>=cutoff]
    verified=[]
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs={ex.submit(verify_fsc_detail,x):x for x in candidates}
        for fut in as_completed(futs):
            try:
                x=fut.result()
                if x: verified.append(x)
            except Exception:
                pass
    status.append({'feed':'금융위/FIU:본문검증','ok':True,'count':len(verified),'method':'body_relevance'})
    return dedupe(verified,400), status



# ---------- 5.8.9 금융정보분석원(KoFIU) 공식 보도자료 ----------
KOFIU_BOARD_URL = "https://www.kofiu.go.kr/kor/notification/report.do"

def kofiu_list_url(page=1):
    return KOFIU_BOARD_URL + '?' + urllib.parse.urlencode({'pageIndex': str(page)})

def parse_kofiu_board_page(raw_html):
    rows = []
    pat = re.compile(
        r'<a\b[^>]*href=["\']([^"\']*report_view\.do\?[^"\']*ntcnYardOrdrNo=\d+[^"\']*)["\'][^>]*>(.*?)</a>',
        re.I | re.S
    )
    matches = list(pat.finditer(raw_html))
    seen = set()
    for i, m in enumerate(matches):
        href = html_unescape(m.group(1)).replace('&amp;','&')
        title = clean_title(strip_html(m.group(2)))
        title = re.sub(r'^\s*\[보도자료\]\s*', '', title).strip()
        if not title or len(title) < 4:
            continue
        link = urllib.parse.urljoin("https://www.kofiu.go.kr", href)
        if link in seen:
            continue
        seen.add(link)
        next_pos = matches[i+1].start() if i+1 < len(matches) else min(len(raw_html), m.end()+3000)
        chunk = strip_html(raw_html[m.end():next_pos])
        dm = re.search(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', chunk)
        if not dm:
            continue
        date = f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}T00:00:00+09:00"
        rows.append({
            'region':'공식자료','query':'FIU','official_source':'FIU','official_sources':['FIU'],
            'title':title,'source':'FIU','date':date,'link':link,
            'tags':classify(title),'collection_method':'verified_kofiu_board'
        })
    return rows

def verify_kofiu_detail(item):
    try:
        raw = fetch_url(item['link'], timeout=10).decode('utf-8', errors='ignore')
        text = strip_html(raw)
        if len(text) < 250:
            return None
        title = item.get('title','')
        if key_title(title) and key_title(title) not in key_title(text):
            return None
        context = (title + ' ' + text[:12000]).strip()
        if not (official_relevant(title) or v55_practical_info(title) or v49_practical_aml(title)
                or re.search(r'금융정보분석원|\bFIU\b|자금세탁|특금법|가상자산사업자|보이스피싱', context, re.I)):
            return None
        x = dict(item)
        x['tags'] = classify(context)
        return x
    except Exception:
        return None

def exact_kofiu_detail_from_item(item):
    blob = html_unescape(ET.tostring(item, encoding='unicode'))
    try:
        blob += ' ' + urllib.parse.unquote(blob)
    except Exception:
        pass
    m = re.search(
        r'https?://(?:www\.)?kofiu\.go\.kr/kor/notification/report_view\.do\?[^"\'<>\s]*ntcnYardOrdrNo=\d+[^"\'<>\s]*',
        blob, re.I
    )
    return html_unescape(m.group(0)).replace('&amp;','&') if m else ''

def collect_kofiu_fallback():
    q = 'site:kofiu.go.kr/kor/notification/report_view.do (자금세탁 OR AML OR FIU OR 특금법 OR 가상자산사업자 OR 보이스피싱)'
    params = urllib.parse.urlencode({'q':q+' when:90d','hl':'ko','gl':'KR','ceid':'KR:ko'})
    data = fetch_url('https://news.google.com/rss/search?' + params, timeout=10)
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        title = clean_title(it.findtext('title') or '')
        title = re.sub(r'^\s*\[보도자료\]\s*', '', title).strip()
        link = exact_kofiu_detail_from_item(it)
        if not title or not link:
            continue
        out.append({
            'region':'공식자료','query':'FIU','official_source':'FIU','official_sources':['FIU'],
            'title':title,'source':'FIU','date':it.findtext('pubDate') or '',
            'link':link,'tags':classify(title),'collection_method':'kofiu_exact_fallback'
        })
    return out

def collect_kofiu_official(backfill, cutoff):
    max_pages = 20 if backfill else 2
    candidates, status = [], []
    for page in range(1, max_pages + 1):
        try:
            raw = fetch_url(kofiu_list_url(page), timeout=10).decode('utf-8', errors='ignore')
            rows = parse_kofiu_board_page(raw)
            candidates.extend(rows)
            status.append({'feed':f'FIU:목록:p{page}','ok':True,'count':len(rows),'method':'official_board'})
            dated = [parse_dt(x.get('date')) for x in rows if parse_dt(x.get('date'))]
            if dated and min(dated) < cutoff:
                break
        except Exception as e:
            status.append({'feed':f'FIU:목록:p{page}','ok':False,'count':0,'error':str(e)[:160]})
            break
        time.sleep(0.05)

    candidates = [x for x in dedupe(candidates, 400)
                  if parse_dt(x.get('date')) and parse_dt(x.get('date')) >= cutoff]
    verified = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(verify_kofiu_detail, x) for x in candidates]
        for fut in as_completed(futs):
            try:
                x = fut.result()
                if x:
                    verified.append(x)
            except Exception:
                pass

    if not verified:
        try:
            fallback = collect_kofiu_fallback()
            verified = [x for x in fallback
                        if parse_dt(x.get('date')) and parse_dt(x.get('date')) >= cutoff]
            status.append({'feed':'FIU:fallback','ok':True,'count':len(verified),'method':'exact_official_fallback'})
        except Exception as e:
            status.append({'feed':'FIU:fallback','ok':False,'count':0,'error':str(e)[:160]})

    status.append({'feed':'FIU','ok':True,'count':len(verified),'method':'official_board_or_exact_fallback'})
    return dedupe(verified, 400), status

def official_title_key(title):
    t = html_unescape(title or '')
    t = re.sub(r'^\s*\[보도자료\]\s*', '', t, flags=re.I)
    t = re.sub(r'\b(FIU|KoFIU)\b', '금융정보분석원', t, flags=re.I)
    t = re.sub(r'금융위원회|금융위|금융정보분석원|금융감독원|금감원|DAXA|닥사', ' ', t, flags=re.I)
    t = re.sub(r'공동\s*보도자료|보도자료', ' ', t, flags=re.I)
    return re.sub(r'[^0-9a-z가-힣]+', '', t.lower())

def official_same_release(a, b):
    da, db = parse_dt(a.get('date')), parse_dt(b.get('date'))
    if da and db and abs((da-db).total_seconds()) > 2*86400:
        return False
    ka, kb = official_title_key(a.get('title','')), official_title_key(b.get('title',''))
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if min(len(ka), len(kb)) >= 18 and (ka in kb or kb in ka):
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= 0.90

def official_source_list(x):
    srcs = x.get('official_sources')
    if isinstance(srcs, list) and srcs:
        return [str(s) for s in srcs if s]
    s = x.get('official_source') or x.get('source')
    return [s] if s else []

def merge_official_releases(rows):
    groups = []
    for x0 in sorted(rows, key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True):
        x = dict(x0)
        x['official_sources'] = official_source_list(x)
        g = next((g for g in groups if official_same_release(g['main'], x)), None)
        if not g:
            groups.append({'main':x, 'members':[x]})
            continue
        g['members'].append(x)

        merged_sources = []
        for m in g['members']:
            for s in official_source_list(m):
                if s not in merged_sources:
                    merged_sources.append(s)

        priority = {'FIU':0, '금융위원회':1, '금융감독원':2, 'DAXA':3}
        best = min(g['members'], key=lambda m: min([priority.get(s, 9) for s in official_source_list(m)] or [9]))
        main = dict(best)
        ordered = sorted(merged_sources, key=lambda s: priority.get(s, 9))
        main['official_sources'] = ordered
        main['official_source'] = '·'.join(ordered)
        main['source'] = main['official_source']
        if len(ordered) > 1:
            main['collection_method'] = 'official_multi_source'
        g['main'] = main

    out = [g['main'] for g in groups]
    out.sort(key=lambda x: parse_dt(x.get('date')) or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True)
    return out

FSS_BOARD_URL = "https://www.fss.or.kr/fss/bbs/B0000188/list.do"
FSS_DETAIL_BASE = "https://www.fss.or.kr/fss/bbs/B0000188/view.do"
FSS_SEARCH_KEYWORDS = [
    "자금세탁","AML","가상자산","불법사금융","보이스피싱",
    "대포통장","불공정거래","시세조종","환치기","외국환","범죄수익"
]

def fss_list_url(keyword, page=1):
    return FSS_BOARD_URL + '?' + urllib.parse.urlencode({
        'menuNo':'200218','searchCnd':'1','searchWrd':keyword,'pageIndex':str(page)
    })

def parse_fss_board_page(raw_html, keyword):
    rows = []
    # 금융감독원 공식 보도자료 상세 형식:
    # /fss/bbs/B0000188/view.do?nttId=<id>&menuNo=200218...
    pat = re.compile(
        r'<a\b[^>]*href=["\']([^"\']*B0000188/view\.do\?[^"\']*nttId=\d+[^"\']*)["\'][^>]*>(.*?)</a>',
        re.I | re.S
    )
    matches = list(pat.finditer(raw_html))
    for i, m in enumerate(matches):
        href = html_unescape(m.group(1)).replace('&amp;','&')
        title = clean_title(strip_html(m.group(2)))
        if not title:
            continue
        next_pos = matches[i+1].start() if i+1 < len(matches) else min(len(raw_html), m.end()+2500)
        chunk = strip_html(raw_html[m.end():next_pos])
        dm = re.search(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})', chunk)
        if not dm:
            continue
        date = f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}T00:00:00+09:00"
        link = urllib.parse.urljoin("https://www.fss.or.kr", href)
        rows.append({
            'region':'공식자료','query':keyword,'official_source':'금융감독원','official_sources':['금융감독원'],
            'title':title,'source':'금융감독원','date':date,'link':link,
            'tags':classify(title),'collection_method':'verified_fss_board'
        })
    return rows

def verify_fss_detail(item):
    try:
        raw = fetch_url(item['link'], timeout=10).decode('utf-8', errors='ignore')
        text = strip_html(raw)
        if len(text) < 300:
            return None
        # 정확한 제목이 실제 상세페이지에 존재해야 한다.
        if key_title(item.get('title','')) not in key_title(text):
            return None
        # 5.7.2: 금감원 일반 보도자료가 본문의 공통 문구 때문에 섞이지 않도록
        # 제목 자체가 AML/금융범죄 실무 범위일 때만 채택한다.
        title = item.get('title','')
        if not (official_relevant(title) or v55_practical_info(title) or v49_practical_aml(title)):
            return None
        return item
    except Exception:
        return None

def collect_fss_board(backfill, cutoff, now_utc):
    # 첫 실행은 각 키워드 2페이지, 이후에는 1페이지만 확인.
    # 키워드 검색으로 범위를 좁혀 전체 게시판 수십 페이지를 훑지 않는다.
    max_pages = 12 if backfill else 1
    jobs = [(kw, p) for kw in FSS_SEARCH_KEYWORDS for p in range(1, max_pages+1)]
    candidates, status = [], []

    def fetch_one(job):
        kw, page = job
        raw = fetch_url(fss_list_url(kw, page), timeout=12).decode('utf-8', errors='ignore')
        return kw, page, parse_fss_board_page(raw, kw)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_one, j): j for j in jobs}
        for fut in as_completed(futs):
            kw, page = futs[fut]
            try:
                _, _, rows = fut.result()
                candidates.extend(rows)
                status.append({'feed':f'금융감독원:{kw}:p{page}','ok':True,'count':len(rows),'method':'official_board'})
            except Exception as e:
                status.append({'feed':f'금융감독원:{kw}:p{page}','ok':False,'count':0,'error':str(e)[:160]})

    candidates = [
        x for x in dedupe(candidates, 500)
        if parse_dt(x.get('date')) and parse_dt(x.get('date')) >= cutoff
    ]
    verified = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(verify_fss_detail, x) for x in candidates]
        for fut in as_completed(futs):
            try:
                x = fut.result()
                if x:
                    verified.append(x)
            except Exception:
                pass
    return dedupe(verified, 300), status

def exact_fss_detail_from_item(item):
    blob = html_unescape(ET.tostring(item, encoding='unicode'))
    try:
        blob += ' ' + urllib.parse.unquote(blob)
    except Exception:
        pass
    m = re.search(
        r'https?://(?:www\.)?fss\.or\.kr/fss/bbs/B0000188/view\.do\?[^"\'<>\s]*nttId=\d+[^"\'<>\s]*',
        blob, re.I
    )
    if not m:
        return ''
    return html_unescape(m.group(0)).replace('&amp;','&')

def collect_fss_fallback():
    # 공식 게시판이 일시적으로 timeout일 때만 보조적으로 활용할 정확한 FSS 상세링크 검색.
    q = '("자금세탁" OR AML OR "가상자산" OR "불법사금융" OR "보이스피싱" OR "불공정거래" OR "환치기") site:fss.or.kr/fss/bbs/B0000188'
    params = urllib.parse.urlencode({'q':q+' when:90d','hl':'ko','gl':'KR','ceid':'KR:ko'})
    data = fetch_url('https://news.google.com/rss/search?' + params, timeout=10)
    root = ET.fromstring(data)
    out = []
    for it in root.findall('.//item')[:100]:
        title = clean_title(it.findtext('title') or '')
        link = exact_fss_detail_from_item(it)
        if not title or not link:
            continue
        out.append({
            'region':'공식자료','query':'금융감독원','official_source':'금융감독원','official_sources':['금융감독원'],
            'title':title,'source':'금융감독원','date':it.findtext('pubDate') or '',
            'link':link,'tags':classify(title),'collection_method':'fss_exact_fallback'
        })
    return out

# ---------- 5.5 DAXA 공식 보도자료 ----------
DAXA_LIST_URL = 'https://www.kdaxa.org/support/press.php?boardid=news&category=&offset={offset}&sk=&sw='

def daxa_relevant(title):
    t=(title or '').lower()
    keep=re.search(r'미신고|불법|범죄|자금세탁|\baml\b|보이스피싱|통신사기|피해환급법|시세조종|이상거래|제도이행평가|트래블룰|\bvasp\b|가상자산사업자|해외\s*거래소|외환\s*전산망|api key', t)
    low=re.search(r'세미나|자료집|정책\s*자료집|전망|포럼|컨퍼런스', t)
    return bool(keep) and not bool(low)

def parse_daxa_list(raw_html):
    rows=[]
    pat=re.compile(r'<a\b[^>]*href=["\']([^"\']*press\.php\?[^"\']*mode=view[^"\']*idx=\d+[^"\']*)["\'][^>]*>(.*?)</a>', re.I|re.S)
    ms=list(pat.finditer(raw_html))
    for i,m in enumerate(ms):
        href=html_unescape(m.group(1)).replace('&amp;','&')
        title=clean_title(strip_html(m.group(2)))
        title=re.sub(r'^\[?\d{6}\]?\s*','',title).strip()
        if not title or not daxa_relevant(title):
            continue
        nxt=ms[i+1].start() if i+1<len(ms) else min(len(raw_html),m.end()+1800)
        chunk=strip_html(raw_html[m.end():nxt])
        dm=re.search(r'(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})',chunk)
        if not dm:
            continue
        date=f"{int(dm.group(1)):04d}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}T00:00:00+09:00"
        rows.append({
            'region':'공식자료','query':'DAXA','official_source':'DAXA','official_sources':['DAXA'],'title':title,'source':'DAXA',
            'date':date,'link':urllib.parse.urljoin('https://www.kdaxa.org/support/',href),
            'tags':classify(title),'collection_method':'verified_daxa_board'
        })
    return rows

def verify_daxa_detail(item):
    try:
        raw=fetch_url(item['link'],timeout=10).decode('utf-8',errors='ignore')
        text=strip_html(raw)
        if len(text)<180 or key_title(item.get('title','')) not in key_title(text):
            return None
        return item
    except Exception:
        return None

def collect_daxa_official(cutoff):
    candidates=[]; status=[]
    # 현재 게시 빈도상 첫 20건이면 최근 90일을 충분히 덮는다.
    for offset in range(0,200,10):
        try:
            raw=fetch_url(DAXA_LIST_URL.format(offset=offset),timeout=10).decode('utf-8',errors='ignore')
            rows=parse_daxa_list(raw)
            candidates.extend(rows)
            status.append({'feed':f'DAXA:offset{offset}','ok':True,'count':len(rows),'method':'official_board'})
        except Exception as e:
            status.append({'feed':f'DAXA:offset{offset}','ok':False,'count':0,'error':str(e)[:160]})
    candidates=[x for x in dedupe(candidates,100) if parse_dt(x.get('date')) and parse_dt(x.get('date'))>=cutoff]
    verified=[]
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs=[ex.submit(verify_daxa_detail,x) for x in candidates]
        for fut in as_completed(futs):
            try:
                x=fut.result()
                if x: verified.append(x)
            except Exception:
                pass
    return dedupe(verified,100), status

def run_regression_selfcheck():
    """네트워크 없이 핵심 누락/과수집 사례를 코드 수준에서 회귀검증한다."""
    keep_cases = [
        ('미신고 불법 코인거래소 82% 적발 후 국내서 버젓이 영업 중', '세계일보', 'segye.com'),
        ('스테이블코인으로 올영·다이소 상품권 깡…금융위 규제 검토', '이데일리', 'edaily.co.kr'),
        ('FIU, 자금세탁 의심거래 정보 은행에 공유한다…특금법 개정 추진', '아시아경제', 'asiae.co.kr'),
        ('보이스피싱범들, 상품권이 막히자 출금 못 막는 외화계좌로 튀었다…자금세탁 새 먹잇감', '헤럴드경제', 'heraldcorp.com'),
        ('네이버·토스·카카오페이, 해외결제 자금세탁 관리 부실…경영 유의', '뉴스핌', 'newspim.com'),
        ('쇼핑몰 계정 도용해 결제하고 현금화…결제대행사 세탁 통로 차단', '라온신문', 'raonnews.com'),
    ]
    failed = []
    for title, src, dom in keep_cases:
        context = title
        category = infer_news_category(title)
        practical = v55_practical_info(context)
        if not category and practical:
            category = v56_practical_category(context)
        gate = bool(v49_practical_aml(title) or practical)
        trust = bool(trusted_news_source(src, dom) or strong_unlisted_source_ok(title, context, src, dom))
        if not (category and gate and trust):
            failed.append(title)
    # 5.8.3 상품권 단독 오탐 방지: 상품권 자체는 AML 신호가 아니다.
    reject_news = [
        '도천동 통우회, 추석맞이 상품권 기탁',
        '파주 카페 사망 사건, 상품권 사기로 수사 확대…운영자 구속',
        '파주 카페 냉동 컨테이너서 60대 여성 숨진 채 발견…경찰, 위조 상품권 사기까지 수사 확대',
    ]
    for title in reject_news:
        if not v581_obvious_noise(title):
            failed.append('뉴스 오탐:'+title)

    # 공식자료 과수집 방지 대표 샘플
    reject_official = [
        'PF보증의 주택공급으로 이연한 금융위원장, 김포 PF사업장 점검 방문',
        '2026년 8월 가계대출 동향 점검회의 개최',
        'IFRS17 체계 구비가 보험산업에 미치는 영향 점검'
    ]
    for title in reject_official:
        if official_relevant(title) or v55_practical_info(title) or v49_practical_aml(title):
            failed.append('공식자료 과수집:'+title)
    if failed:
        raise RuntimeError('5.8 regression self-check failed: ' + ' | '.join(failed))
    print('SELF_CHECK OK', len(keep_cases), 'keep cases +', len(reject_official), 'official reject cases')

def main():
    run_regression_selfcheck()
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
    practical_backfill = old.get('practical_backfill_version') != '5.8.0'
    for name, query in QUERIES:
        try:
            items = fetch_query(name, query, 1)
            all_items.extend(items)
            status.append({'feed': name, 'ok': True, 'count': len(items)})
            print('NEWS', name, len(items))
        except Exception as e:
            status.append({'feed': name, 'ok': False, 'count': 0, 'error': str(e)[:180]})
            print('NEWS ERROR', name, e)
        time.sleep(0.10)

    # 색인이 조금 늦게 잡히는 기사까지 회수하도록 평상시에도 3일을 겹쳐 확인한다.
    practical_window = RETENTION_DAYS if practical_backfill else 3
    for name, query in PRACTICAL_QUERIES:
        try:
            items = fetch_query(name, query, practical_window)
            all_items.extend(items)
            status.append({'feed': '실무:'+name, 'ok': True, 'count': len(items), 'window_days': practical_window})
            print('PRACTICAL', name, len(items), 'window', practical_window)
        except Exception as e:
            status.append({'feed': '실무:'+name, 'ok': False, 'count': 0, 'error': str(e)[:180]})
            print('PRACTICAL ERROR', name, e)
        time.sleep(0.10)

    # 5.8 보조 발견경로: 누락 사례가 실제로 발생했던 주요 매체를 좁은 site: 검색으로 재확인.
    # 첫 실행은 90일, 이후에는 3일만 겹쳐 검색해 요청량을 제한한다.
    focused_window = RETENTION_DAYS if practical_backfill else 3
    for domain in SOURCE_FOCUSED_DOMAINS:
        try:
            q = f'site:{domain} ' + SOURCE_FOCUSED_QUERY
            items = fetch_query('출처집중:'+domain, q, focused_window)
            all_items.extend(items)
            status.append({'feed':'출처집중:'+domain,'ok':True,'count':len(items),'window_days':focused_window})
            print('SOURCE_FOCUSED', domain, len(items), 'window', focused_window)
        except Exception as e:
            status.append({'feed':'출처집중:'+domain,'ok':False,'count':0,'error':str(e)[:180]})
            print('SOURCE_FOCUSED ERROR', domain, e)
        time.sleep(0.08)

    merged_news = []
    for x in all_items + existing:
        d = parse_dt(x.get('date'))
        if d and d < cutoff:
            continue
        # 과거 버전에서 저장된 스팸/SEO 출처도 다시 정리한다.
        src_name = x.get('source','')
        src_domain = x.get('source_domain','')
        title0 = x.get('title','')
        context0 = (title0 + ' ' + x.get('description','')).strip()
        if not (trusted_news_source(src_name, src_domain) or strong_unlisted_source_ok(title0, context0, src_name, src_domain)):
            continue
        if v48_nonarticle(title0, x.get('source_url',''), src_domain):
            continue
        if low_quality_news_source(title0, src_name, src_domain):
            continue
        category = infer_news_category(title0) or infer_news_category(context0)
        practical_ok = v55_practical_info(title0) or v55_practical_info(context0)
        if not category and practical_ok:
            category = v56_practical_category(context0)
        if not category:
            continue
        if v581_obvious_noise(title0):
            continue
        if not (v49_practical_aml(title0) or v49_practical_aml(context0) or practical_ok):
            continue
        x = dict(x)
        x['query'] = category
        x['tags'] = classify(x.get('title',''))
        merged_news.append(x)
    news = dedupe(merged_news, 1200)

    # ---------- 공식자료 ----------
    # v4.3 이전 공식자료는 잘못된 링크/날짜가 섞였으므로 처음 한 번은 폐기 후 90일 재구축.
    audit_version = '5.8.9-official-90d-audit-1'
    prior_backfill_ok = bool(old.get('official_90d_audit_complete')) and old.get('official_90d_audit_version') == audit_version
    backfill = not prior_backfill_ok

    fsc_items, official_status = collect_fsc_official(backfill, cutoff, now_utc)

    kofiu_items, kofiu_status = collect_kofiu_official(backfill, cutoff)
    official_status.extend(kofiu_status)

    daxa_items, daxa_status = collect_daxa_official(cutoff)
    official_status.extend(daxa_status)
    try:
        fss_items, fss_status = collect_fss_board(backfill, cutoff, now_utc)
        official_status.extend(fss_status)
        # 게시판이 일시적으로 접근 불가하거나 결과가 0건이면 정확한 공식 상세링크 fallback.
        if not fss_items:
            fallback = collect_fss_fallback()
            fss_items = dedupe(fallback, 300)
            official_status.append({'feed':'금융감독원:fallback','ok':True,'count':len(fss_items),'method':'exact_official_fallback'})
        official_status.append({'feed':'금융감독원','ok':True,'count':len(fss_items),'method':'official_board'})
    except Exception as e:
        try:
            fss_items = collect_fss_fallback()
            official_status.append({'feed':'금융감독원:fallback','ok':True,'count':len(fss_items),'method':'exact_official_fallback'})
        except Exception as e2:
            fss_items = []
            official_status.append({'feed':'금융감독원','ok':False,'count':0,'error':(str(e)+' / '+str(e2))[:160]})

    if backfill:
        # 오래된/잘못된 v4.2 공식자료는 사용하지 않는다.
        official_seed = []
    else:
        official_seed = existing_official

    official_merged = []
    for x in kofiu_items + fsc_items + fss_items + daxa_items + official_seed:
        d = parse_dt(x.get('date'))
        if not d or d < cutoff:
            continue
        # 이전 실행에서 과수집된 공식자료도 다음 실행 때 즉시 청소한다.
        title = x.get('title','')
        if not (official_relevant(title) or v55_practical_info(title) or v49_practical_aml(title) or x.get('official_source') == 'DAXA'):
            continue
        official_merged.append(x)
    official = merge_official_releases(dedupe(official_merged, 800))[:500]

    payload = {
        'updated_at': now_utc.isoformat(),
        'retention_days': RETENTION_DAYS,
        'feed_status': status,
        'official_status': official_status,
        'official_backfill_complete': True,
        'official_backfill_version': '5.8.9',
        'official_90d_audit_complete': True,
        'official_90d_audit_version': audit_version,
        'official_collection_mode': '90d_backfill' if backfill else '7d_incremental',
        'practical_backfill_complete': True,
        'practical_backfill_version': '5.8.0',
        'practical_collection_mode': '90d_backfill' if practical_backfill else '3d_overlap_incremental',
        'count': len(news),
        'official_count': len(official),
        'items': news,
        'official_items': official,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('DONE news=', len(news), 'official=', len(official), 'official_mode=', payload['official_collection_mode'], 'practical_mode=', payload['practical_collection_mode'])

if __name__ == '__main__':
    main()
