import streamlit as st
import pandas as pd
import random
from datetime import datetime
import os

# 1. 페이지 및 기본 설정
st.set_page_config(page_title="Game Mode Session Manager", page_icon="🎮", layout="wide")

st.title("🎮 POSITIVE / NEGATIVE 게임 세션 매니저")
st.markdown("<b>POSITIVE</b> / <b>NEGATIVE</b> 게임 타입을 무작위로 추첨하고, 6회차 진행 및 PASS/FAIL 실행 로그를 기록하는 도구입니다.", unsafe_allow_html=True)

# 저장할 CSV 파일 및 폴더 경로 설정
SAVE_DIR = "/Users/tj/test_v5/tests"
LOG_FILE = os.path.join(SAVE_DIR, "game_session_logs.csv")

os.makedirs(SAVE_DIR, exist_ok=True)

# 세션 상태(Session State) 초기화
if "sessions" not in st.session_state:
    st.session_state.sessions = []

if "history_df" not in st.session_state:
    if os.path.exists(LOG_FILE):
        df_loaded = pd.read_csv(LOG_FILE)
        # 구버전 CSV의 'status' 컬럼을 'result'로 자동 마이그레이션 (KeyError 방지)
        if "status" in df_loaded.columns and "result" not in df_loaded.columns:
            df_loaded = df_loaded.rename(columns={"status": "result"})
        st.session_state.history_df = df_loaded
    else:
        st.session_state.history_df = pd.DataFrame(columns=["session_id", "turn", "game_type", "result", "timestamp"])

# 2. 메인 탭 구성
tab1, tab2 = st.tabs(["🕹️ 게임 실행 (6회차)", "📊 기록 관리 & CSV 다운로드"])

with tab1:
    # 요구사항 2: 사이드바 제거 및 메인 상단에 생성 버튼 배치
    col_btn, col_info = st.columns([1, 2])
    with col_btn:
        if st.button("🎲 새로운 6회 게임 무작위 생성", use_container_width=True):
            modes = ["POSITIVE", "NEGATIVE"]
            new_session_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            st.session_state.current_session_id = new_session_id
            
            st.session_state.sessions = [
                {
                    "session_id": new_session_id,
                    "turn": i + 1,
                    "game_type": random.choice(modes),
                    "result": "미실행",
                    "timestamp": "-"
                }
                for i in range(6)
            ]
            st.rerun()

    if not st.session_state.sessions:
        st.info("상단의 **[새로운 6회 게임 무작위 생성]** 버튼을 눌러주세요.")
    else:
        st.subheader(f"현재 진행 중인 세션 ID: {st.session_state.sessions[0]['session_id']}")
        
        # 요구사항 3: 6개 카드가 한 화면에 일관되게 노출되도록 배치
        cols = st.columns(3)
        for idx, item in enumerate(st.session_state.sessions):
            col = cols[idx % 3]
            with col:
                with st.container(border=True):
                    # 요구사항 1: POSITIVE는 RED (#d32f2f), NEGATIVE는 BLUE (#1976d2) 로 변경
                    badge_color = "#d32f2f" if item["game_type"] == "POSITIVE" else "#1976d2"
                    st.markdown(f"### {item['turn']} 회차")
                    st.markdown(f"<h2 style='color:{badge_color}; margin:0;'>{item['game_type']}</h2>", unsafe_allow_html=True)
                    
                    # 결과 색상 표기
                    res_color = "gray"
                    if item["result"] == "PASS":
                        res_color = "green"
                    elif item["result"] == "FAIL":
                        res_color = "red"
                        
                    st.markdown(f"결과: <b style='color:{res_color};'>{item['result']}</b>", unsafe_allow_html=True)
                    st.caption(f"실행 시간: {item['timestamp']}")
                    
                    # PASS / FAIL 버튼 배치
                    if item["result"] == "미실행":
                        btn_col1, btn_col2 = st.columns(2)
                        
                        if btn_col1.button(f"✅ PASS", key=f"pass_{idx}", use_container_width=True):
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            item["result"] = "PASS"
                            item["timestamp"] = now_str
                            
                            new_row = pd.DataFrame([item])
                            st.session_state.history_df = pd.concat([st.session_state.history_df, new_row], ignore_index=True)
                            st.session_state.history_df.to_csv(LOG_FILE, index=False)
                            st.rerun()
                            
                        if btn_col2.button(f"❌ FAIL", key=f"fail_{idx}", use_container_width=True):
                            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            item["result"] = "FAIL"
                            item["timestamp"] = now_str
                            
                            new_row = pd.DataFrame([item])
                            st.session_state.history_df = pd.concat([st.session_state.history_df, new_row], ignore_index=True)
                            st.session_state.history_df.to_csv(LOG_FILE, index=False)
                            st.rerun()

with tab2:
    st.subheader("📁 누적 실행 데이터 로그")
    st.caption(f"저장 경로: `{LOG_FILE}`")
    
    if st.session_state.history_df.empty:
        st.write("아직 실행 기록이 없습니다.")
    else:
        df = st.session_state.history_df
        st.dataframe(df, use_container_width=True)
        
        # 요약 통계
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 실행 회차", f"{len(df)} 회")
        c2.metric("PASS 수", f"{len(df[df['result'] == 'PASS'])} 회")
        c3.metric("FAIL 수", f"{len(df[df['result'] == 'FAIL'])} 회")
        c4.metric("POSITIVE 수", f"{len(df[df['game_type'] == 'POSITIVE'])} 회")
        
        # CSV 다운로드
        csv_bytes = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 데이터 CSV로 다운로드",
            data=csv_bytes,
            file_name=f"game_mode_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )