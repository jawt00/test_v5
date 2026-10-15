import streamlit as st
import pandas as pd
import random
from datetime import datetime
import os

# 1. 페이지 및 기본 설정
st.set_page_config(page_title="Game Mode Session Manager", page_icon="🎮", layout="wide")

st.title("🎮 POSITIVE / NEGATIVE 게임 세션 매니저")
st.markdown("<b>POSITIVE</b> / <b>NEGATIVE</b> 게임 타입 및 <b>[숫자, 문자]</b> 모드를 무작위로 추첨하고, 8회차 진행 로그를 기록하는 도구입니다.", unsafe_allow_html=True)

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
        # 구버전 CSV 컬럼 호환 처리
        if "status" in df_loaded.columns and "result" not in df_loaded.columns:
            df_loaded = df_loaded.rename(columns={"status": "result"})
        st.session_state.history_df = df_loaded
    else:
        st.session_state.history_df = pd.DataFrame(columns=["session_id", "turn", "game_type", "number_type", "char_type", "result", "timestamp"])

# 2. 메인 탭 구성
tab1, tab2 = st.tabs(["🕹️ 게임 실행 (8회차)", "📊 기록 관리 & CSV 다운로드"])

with tab1:
    col_btn, col_info = st.columns([1, 2])
    with col_btn:
        if st.button("🎲 새로운 8회 게임 무작위 생성", use_container_width=True):
            modes = ["POSITIVE", "NEGATIVE"]
            chars = ["B", "P"]
            new_session_id = f"SESS_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            st.session_state.current_session_id = new_session_id
            
            # 숫자 타입 (9 / 10 교대)
            start_num = random.choice([9, 10])
            num_types = [9 if (i % 2 == 0) else 10 for i in range(8)] if start_num == 9 else [10 if (i % 2 == 0) else 9 for i in range(8)]
            
            # 문자 타입 (B 또는 P)을 모든 회차마다 완전히 무작위(Random) 선택
            char_types = [random.choice(chars) for _ in range(8)]
            
            # 1~8회차 세션 생성 (동일 타입 연속 출현 방지 규칙 적용)
            sessions_data = []
            last_game_type = None

            for i in range(8):
                if last_game_type is None:
                    available_modes = modes
                else:
                    available_modes = [m for m in modes if m != last_game_type]
                
                chosen_mode = random.choice(available_modes)
                last_game_type = chosen_mode
                
                sessions_data.append({
                    "session_id": new_session_id,
                    "turn": i + 1,
                    "game_type": chosen_mode,          
                    "number_type": num_types[i], 
                    "char_type": char_types[i],   
                    "result": "미실행",
                    "timestamp": "-"
                })

            st.session_state.sessions = sessions_data
            st.rerun()

    if not st.session_state.sessions:
        st.info("상단의 **[새로운 8회 게임 무작위 생성]** 버튼을 눌러주세요.")
    else:
        st.subheader(f"현재 진행 중인 세션 ID: {st.session_state.sessions[0]['session_id']}")
        
        # 8개 카드가 깔끔하게 배치되도록 4열 Layout 사용
        cols = st.columns(4)
        for idx, item in enumerate(st.session_state.sessions):
            col = cols[idx % 4]
            with col:
                with st.container(border=True):
                    badge_color = "#d32f2f" if item["game_type"] == "POSITIVE" else "#1976d2"
                    
                    st.markdown(f"### {item['turn']} 회차")
                    
                    st.markdown(
                        f"<div style='margin-bottom: 10px;'>"
                        f"<div style='font-size:20px; font-weight:bold; color:{badge_color}; margin-bottom:4px;'>{item['game_type']}</div>"
                        f"<div style='font-size:24px; font-weight:bold; color:#111;'>[ {item['number_type']} , {item['char_type']} ]</div>"
                        f"</div>", 
                        unsafe_allow_html=True
                    )
                    
                    res_color = "gray"
                    if item["result"] == "PASS":
                        res_color = "green"
                    elif item["result"] == "FAIL":
                        res_color = "red"
                        
                    st.markdown(f"결과: <b style='color:{res_color};'>{item['result']}</b>", unsafe_allow_html=True)
                    st.caption(f"실행 시간: {item['timestamp']}")
                    
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
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("총 실행 회차", f"{len(df)} 회")
        c2.metric("PASS 수", f"{len(df[df['result'] == 'PASS'])} 회")
        c3.metric("FAIL 수", f"{len(df[df['result'] == 'FAIL'])} 회")
        c4.metric("POSITIVE 수", f"{len(df[df['game_type'] == 'POSITIVE'])} 회")
        
        csv_bytes = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 데이터 CSV로 다운로드",
            data=csv_bytes,
            file_name=f"game_mode_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )