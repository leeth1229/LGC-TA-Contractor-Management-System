![Open in Streamlit](lgc-ta-contractor-management-system-oqh6fn5lr98n5rah37hnkz.streamlit.app)

🎈 LGC TA 협력사 관리 시스템
LG화학 TA(Turn Around) 기간 중 협력사 작업 현황, 인원, 중장비 사용 현황을 통합 관리하기 위한 웹 기반 시스템입니다.

👨‍💻 개발 목적
본 시스템은 LG화학 TA 기간 동안 발생하는 협력사 작업 데이터를 체계적으로 관리하고,
이제 것 수기로 작업현황을 관리하는 것이 아닌 간단한 앱으로 협력사와 공유해 실시간 정보 수집 목적을 위함입니다.
현장 관리자의 업무 효율 향상과 협력사 작업 현황의 실시간 모니터링을 목적으로 개발되었습니다.

📌 주요 기능
🔐 로그인 및 권한 관리
관리자 로그인(admin1,2,3)
협력사 로그인(별도 등록 필요)
사용자 권한별 메뉴 접근 제어
관리자 전용 기능 제공

📊 대시보드
공장별 작업 현황을 실시간으로 확인할 수 있습니다.

제공 지표
- 총 작업 건수
- 작업 진척도
- 등록 협력사 수
- 총 출입 인원
- 총 출입 중장비 대수
- 평균 작업 시작/종료 시간

조회 기능
- 공장별 필터링
- 기간별 조회

시각화
- 작업 진척도 추이
- 작업 인원 추이
- 공장별 작업 현황 분석

📋 작업 현황 관리
일일 작업 보고 데이터를 기반으로 작업 현황을 관리합니다.

관리 항목
- 작업일자
- 공장
- 협력사
- 공사명
- 작업명
- 출입 인원
- 작업 상태
- 중장비 사용 현황
- 작업 진척도
- 작업사항
- 작업 시작/종료 시간

📈 데이터 분석
작업 데이터를 활용하여 다양한 현황을 시각적으로 제공합니다.
- 작업 진척도 분석
- 인원 투입 현황 분석
- 공장별 작업 실적 분석
- 기간별 작업 추이 분석

🛠️ 기술 스택
구분	            기술
Frontend	        Streamlit
Language	        Python
Data Processing	    Pandas
Visualization	    Plotly
Storage	            CSV / Excel
Deployment	        Streamlit Community Cloud

🚀 설치 방법
1. 저장소 복제
git clone https://github.com/your-repository/LGC-TA-Contractor-Management-System.git
cd LGC-TA-Contractor-Management-System

2. 패키지 설치
pip install -r requirements.txt

3. 애플리케이션 실행
streamlit run streamlit_app.py

📂 프로젝트 구조
LGC-TA-Contractor-Management-System
│
├── streamlit_app.py
├── requirements.txt
├── README.md
│
├── data/
│   ├── announcements.csv
│   ├── daily_reports.csv
│   └── evaluations.csv
│
└── assets/

🎯 기대 효과
- TA 기간 중 협력사 작업 현황 실시간 관리
- 작업 진척도 가시성 향상
- 중장비 사용 현황 통합 관리
- 데이터 기반 의사결정 지원
- 현장 관리자 업무 효율 향상

/이상/
