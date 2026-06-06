from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import urllib.parse
import re
import string
import random
import time
import math
from datetime import datetime

app = Flask(__name__)
CORS(app)

ROOMS_DB = {}

# ==========================================
# 🧹 [최적화 1] 메모리 관리 (방 청소 로직)
# ==========================================
def cleanup_old_rooms():
    """생성된 지 1시간(3600초)이 지난 방을 메모리에서 삭제합니다."""
    current_time = time.time()
    expired_rooms = [rid for rid, data in ROOMS_DB.items() if current_time - data["created_at"] > 3600]
    for rid in expired_rooms:
        del ROOMS_DB[rid]

def generate_room_id(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

# (기존 extract_racers 함수 원본 그대로 유지)
def extract_racers(url):
    gall_id = None
    gall_no = None

    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    
    if 'id' in qs and 'no' in qs:
        gall_id = qs['id'][0]
        gall_no = qs['no'][0]
    else:
        match = re.search(r'/(?:board|mini|mgallery)/([^/?]+)/([^/?]+)', url)
        if match:
            gall_id = match.group(1)
            gall_no = match.group(2)
            
    if not gall_id or not gall_no:
        return [], "URL에서 게시판 ID와 글 번호를 찾을 수 없습니다. 올바른 링크인지 확인해주세요."

    target_url = url
    if "m.dcinside.com" in url:
        target_url = f"https://gall.dcinside.com/board/view/?id={gall_id}&no={gall_no}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://gall.dcinside.com/"
    }
    session = requests.Session()
    
    try:
        res = session.get(target_url, headers=headers, timeout=5)
        res.raise_for_status()
        
        if "location.replace" in res.text:
            redirect_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]\)", res.text)
            if redirect_match:
                target_url = redirect_match.group(1)
                if target_url.startswith('/'):
                    target_url = "https://gall.dcinside.com" + target_url
                res = session.get(target_url, headers=headers, timeout=5)
                res.raise_for_status()
                
    except Exception as e:
        return [], f"게시글 접속 오류: {e}"
        
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    token_input = soup.find('input', {'id': 'e_s_n_o'})
    if token_input:
        e_s_n_o = token_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match: e_s_n_o = match.group(1)

    final_url = res.url
    gall_type = "G"
    if "mgallery" in target_url or "mgallery" in final_url: 
        gall_type = "M"
    elif "mini" in target_url or "mini" in final_url: 
        gall_type = "MI"

    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy()
    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
    ajax_headers["Referer"] = final_url
    
    payload = {
        "id": gall_id, "no": gall_no, "cmt_id": gall_id, "cmt_no": gall_no,
        "e_s_n_o": e_s_n_o, "_GALLTYPE_": gall_type, "page": 1
    }
    
    try:
        cmt_res = session.post(ajax_url, data=payload, headers=ajax_headers)
        cmt_data = cmt_res.json()
    except Exception as e:
        return [], f"댓글 API 로드 실패: {e}"
        
    racers = {} 
    
    if not cmt_data or "comments" not in cmt_data or not cmt_data["comments"]:
        return [], "이 게시글에는 고닉/반고닉 댓글이 없습니다."
        
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict): continue
        uid = cmt.get("user_id", "")
        nick = cmt.get("name", "ㅇㅇ")
        reg_date = cmt.get("reg_date", "")
        
        if not uid: continue
        
        user_key = f"{nick}({uid})"
        if user_key not in racers:
            racers[user_key] = reg_date
            
    participant_list = [{"name": k, "reg_date": v} for k, v in racers.items()]
    return participant_list, None

@app.route('/api/extract_only', methods=['POST'])
def extract_only():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"success": False, "message": "URL이 필요합니다."}), 400
        
    participants, error = extract_racers(url)
    if error:
        return jsonify({"success": False, "message": error}), 400
    return jsonify({"success": True, "participants": participants})


# ==========================================
# 🐎 [백엔드 코어] 우마무스메 물리 엔진 (Python)
# ==========================================
FPS = 15
DT = 1.0 / FPS

# 각질별 보정치 [도주, 선행, 선입, 추입]
STYLE_MODS = {
    "도주": {"spd": [1.000, 0.980, 0.962], "accel": [1.000, 1.000, 0.996], "hp": 0.95},
    "선행": {"spd": [0.978, 0.991, 0.975], "accel": [0.985, 1.000, 0.996], "hp": 0.89},
    "선입": {"spd": [0.938, 0.998, 0.994], "accel": [0.975, 1.000, 1.000], "hp": 1.00},
    "추입": {"spd": [0.931, 1.000, 1.000], "accel": [0.945, 1.000, 0.997], "hp": 0.995}
}

CONDITIONS = {"최상": 1.05, "양호": 1.02, "보통": 1.00, "저조": 0.98, "최악": 0.95}

class Uma:
    def __init__(self, uid, name, style, raw_spd, raw_stam, raw_pow, raw_guts, raw_int, condition, track_len, start_lane):
        self.id = uid
        self.name = name
        self.style = style
        self.original_style = style
        
        # 💡 컨디션 보정 적용
        c_mod = CONDITIONS.get(condition, 1.0)
        self.spd = raw_spd * c_mod
        self.stam = raw_stam * c_mod
        self.pow = raw_pow * c_mod
        self.guts = raw_guts * c_mod
        self.int = raw_int * c_mod
        
        # 💡 체력 초기화
        self.max_hp = 0.8 * STYLE_MODS[style]["hp"] * self.stam + track_len
        self.hp = self.max_hp
        self.dist = 0.0
        self.lane = start_lane # 0.0 ~ 1.5 폭 (1폭 = 11.25m 기준)
        
        # 💡 스타트 딜레이 및 기본 변수
        self.start_delay = random.uniform(0.0, 0.1)
        self.speed = 0.0
        self.target_speed = 0.0
        
        # 💡 상태 변수
        self.section = 1
        self.phase = 0
        self.is_start_dash = True
        self.is_kakari = False
        self.kakari_timer = 0.0
        self.has_kakari_history = False
        self.is_pace_down = False
        
        self.pos_comp_timer = 0.0
        self.oikurabe_timer = 0.0
        self.oikurabe_ready = 0.0
        
        self.is_spurting = False
        self.is_exhausted = False
        self.active_states = []

class RaceSimulator:
    def __init__(self, runners, track_len):
        self.runners = runners
        self.track_len = track_len
        self.section_len = track_len / 24.0
        self.base_speed = 20.0 - ((track_len - 2000) / 1000.0)
        self.time = 0.0
        self.frames = []
        self.race_mod = 0.0008 * (track_len - 1000) + 1.0

    def run(self):
        safety = 0
        while any(r.dist < self.track_len for r in self.runners) and safety < 30000:
            self.tick()
            self.time += DT
            safety += 1
        return self.frames

    def get_pacemaker(self):
        for style in ["도주", "선행", "선입", "추입"]:
            group = [r for r in self.runners if r.style == style]
            if group:
                return max(group, key=lambda x: x.dist)
        return self.runners[0]

    def tick(self):
        current_frame = {"t": round(self.time, 2), "r": []}
        leader = max(self.runners, key=lambda x: x.dist)
        pacemaker = self.get_pacemaker()

        for r in self.runners:
            if r.dist >= self.track_len:
                continue
            
            r.active_states = []
            prev_section = r.section
            r.section = min(24, math.floor(r.dist / self.section_len) + 1)
            
            if r.section <= 4: r.phase = 0
            elif r.section <= 16: r.phase = 1
            else: r.phase = 2

            # ==========================================
            # 1. 흥분 상태 (Kakari) 판정 (2~9구간 진입 시)
            # ==========================================
            if r.section != prev_section and 2 <= r.section <= 9 and not r.is_kakari and not r.has_kakari_history:
                prob = math.pow(6.5 / math.log10(0.1 * r.int + 1), 2) / 100.0
                if random.random() < prob:
                    r.is_kakari = True
                    r.has_kakari_history = True
                    r.kakari_timer = 12.0
                    
                    # 각질 변환 로직
                    if r.original_style == "선행": r.style = "도주"
                    elif r.original_style == "선입": r.style = "도주" if random.random() < 0.75 else "선행"
                    elif r.original_style == "추입": 
                        rand = random.random()
                        r.style = "도주" if rand < 0.70 else ("선행" if rand < 0.90 else "선입")

            if r.is_kakari:
                r.kakari_timer -= DT
                # 3초마다 55% 확률로 종료 판정 (프레임 쪼개기)
                if int(self.time) % 3 == 0 and self.time % 1 < DT:
                    if random.random() < 0.55:
                        r.kakari_timer = 0
                if r.kakari_timer <= 0:
                    r.is_kakari = False
                    r.style = r.original_style # 원래 각질 복귀

            # ==========================================
            # 2. 포지션 킵 시스템 (1~10구간)
            # ==========================================
            r.is_pace_down = False
            pace_down_mod = 1.0
            if r.section <= 10 and r.id != pacemaker.id:
                dist_diff = pacemaker.dist - r.dist
                lower_bound = 0
                
                if r.style == "선행": lower_bound = 3.0
                elif r.style == "선입": lower_bound = 6.5 * self.race_mod
                elif r.style == "추입": lower_bound = 7.5 * self.race_mod
                
                # 페이스 다운 모드 진입
                if dist_diff < lower_bound:
                    r.is_pace_down = True
                    pace_down_mod = 0.915 if r.phase == 0 else 0.945

            # ==========================================
            # 3. 특수 이벤트 (위치 경쟁 & 근성 경쟁)
            # ==========================================
            # 위치 경쟁 (도주, 스타트 150m ~ 6구간)
            if r.style == "도주" and 150 <= r.dist and r.section <= 6:
                if leader.dist - r.dist <= 5.0 and r.id != leader.id:
                    if r.pos_comp_timer <= 0:
                        r.pos_comp_timer = math.pow(700 * r.guts, 0.5) * 0.012

            if r.pos_comp_timer > 0:
                r.pos_comp_timer -= DT

            # 종반 근성 경쟁 (Oikurabe)
            if r.phase == 2 and r.dist > self.track_len * 0.75: # 최종 직선 진입 가정
                has_rival = False
                for o in self.runners:
                    if o.id != r.id and abs(r.dist - o.dist) <= 3.0 and abs(r.lane - o.lane) <= (4.5 / 11.25) and abs(r.speed - o.speed) <= 0.6:
                        has_rival = True
                        break
                if has_rival:
                    r.oikurabe_ready += DT
                    if r.oikurabe_ready >= 2.0:
                        r.oikurabe_timer = 5.0 # 발동
                else:
                    r.oikurabe_ready = 0.0

            if r.oikurabe_timer > 0:
                r.oikurabe_timer -= DT

            # ==========================================
            # 4. 속도 및 가속도 연산
            # ==========================================
            # 기본 속도 셋업
            spd_mod = STYLE_MODS[r.style]["spd"][r.phase]
            spd_bonus = math.sqrt(500 * r.spd) * 0.002
            
            # 목표 속도 산출
            r.target_speed = self.base_speed * spd_mod * pace_down_mod
            if r.phase == 2:
                r.target_speed += spd_bonus

            # 라스트 스퍼트 목표 속도
            guts_spd_bonus = math.pow(450 * r.guts, 0.597) * 0.0001
            spurt_spd = (self.base_speed * STYLE_MODS[r.style]["spd"][2] + 0.01 * self.base_speed) * 1.05 + spd_bonus + guts_spd_bonus

            # 경쟁 이벤트 보정 추가
            if r.pos_comp_timer > 0:
                r.target_speed += math.pow(500 * r.guts, 0.6) * 0.0001
            if r.oikurabe_timer > 0:
                spurt_spd += math.pow(200 * r.guts, 0.708) * 0.0001
                r.target_speed += math.pow(200 * r.guts, 0.708) * 0.0001

            # 체력 역산 및 스퍼트 진입 판정
            if r.hp <= 0 and not r.is_exhausted:
                r.is_exhausted = True
                r.is_spurting = False
            
            elif r.phase == 2 and not r.is_spurting and not r.is_exhausted:
                late_drain_mod = 1 + 200 / math.sqrt(600 * r.guts)
                spurt_drain = (20 * math.pow(spurt_spd - self.base_speed + 12, 2) / 144.0) * late_drain_mod
                req_hp = max(0, ((self.track_len - r.dist) - 60) / spurt_spd * spurt_drain)
                
                # 스퍼트 가능 지구력 도달 시 스퍼트 개시
                if r.hp >= req_hp: 
                    r.is_spurting = True

            # 최종 적용 타겟 속도
            if r.is_exhausted:
                min_speed = 0.85 * self.base_speed + math.sqrt(200 * r.guts) * 0.001
                r.target_speed = min_speed
            elif r.is_spurting:
                r.target_speed = spurt_spd

            # ==========================================
            # 5. 가속도 및 HP 소모
            # ==========================================
            if self.time < r.start_delay:
                r.speed = 0.0
            else:
                if self.time == r.start_delay:
                    r.speed = 3.0 # 게이트 출발 속도

                # 기본 가속도
                base_accel = math.sqrt(500 * r.pow) * 0.002
                accel = base_accel * STYLE_MODS[r.style]["accel"][r.phase]
                
                # 다릿심 충분 (파워 1200 이상 종반 돌입 시)
                if r.is_spurting and r.pow >= 1200:
                    leg_power = r.pow - 1200
                    leg_bonus = 0.096 if leg_power < 200 else (0.147 if leg_power < 300 else 0.189)
                    if r.has_kakari_history: leg_bonus *= 0.8
                    accel += leg_bonus

                # 스타트 대시
                if r.is_start_dash:
                    if r.speed < self.base_speed * 0.85:
                        accel += 24.0
                    else:
                        r.is_start_dash = False

                # 근성 경쟁 가속도
                if r.oikurabe_timer > 0:
                    accel += math.pow(160 * r.guts, 0.59) * 0.0001

                # 감속 패널티
                decel = 1.2 if r.is_exhausted else (0.5 if r.is_pace_down else (0.8 if r.phase == 1 else 1.0))

                # 속도 적용
                if r.speed < r.target_speed:
                    r.speed = min(r.speed + accel * DT, r.target_speed)
                elif r.speed > r.target_speed:
                    r.speed = max(r.speed - decel * DT, r.target_speed)

                # 지구력(HP) 소모
                if not r.is_exhausted:
                    state_mod = 1.6 if r.is_kakari else (0.6 if r.is_pace_down else 1.0)
                    if r.pos_comp_timer > 0: state_mod *= 1.4 # 위치 경쟁 체력 소모 증가
                    
                    hp_drain = 20 * math.pow(r.speed - self.base_speed + 12, 2) / 144.0 * state_mod
                    if r.phase == 2:
                        hp_drain *= (1 + 200 / math.sqrt(600 * r.guts))
                    
                    r.hp = max(0, r.hp - hp_drain * DT)
                    
                r.dist += r.speed * DT

            # ==========================================
            # 6. 레인 변경 및 추월 로직 (1.5폭 기준)
            # ==========================================
            # 기획자님의 복잡한 A* 기반 탐색을 수학적으로 축약
            target_lane = r.lane
            is_blocked = False
            
            for o in self.runners:
                if o.id != r.id and 0 < o.dist - r.dist <= 20.0 and abs(o.lane - r.lane) <= (3.5 / 11.25) and r.speed > o.speed:
                    is_blocked = True
                    break
                    
            if r.is_exhausted:
                target_lane = r.lane
            elif is_blocked:
                target_lane = r.lane + (2.0 / 11.25) # 바깥쪽 추월 시도
                r.target_speed *= 1.05 # 추월 모드 속도 보정
            elif r.is_pace_down:
                target_lane += 0.18
            else:
                target_lane -= 0.05 # 평소 인코스 파고들기

            # 하드 리미트 (0.0 ~ 1.5 폭 제한)
            target_lane = max(0.0, min(target_lane, 1.5))
            
            lane_move_speed = 0.02 * (0.3 + 0.001 * r.pow) * (1.0 + 0.01 * 1) * 30.0 # 스케일링 보정
            if abs(r.lane - target_lane) > 0.05:
                r.lane += lane_move_speed * DT * (1 if r.lane < target_lane else -1)
            r.lane = max(0.0, min(r.lane, 1.5))

            # 상태 저장 (프론트엔드 전송용)
            if r.start_delay >= 0.08 and self.time < 1.0: r.active_states.append("LateStart")
            if r.is_start_dash: r.active_states.append("Dash")
            if r.is_spurting: r.active_states.append("Spurt")
            if r.is_exhausted: r.active_states.append("Exhaust")
            if r.is_kakari: r.active_states.append("Kakari")
            if r.is_pace_down: r.active_states.append("PaceDown")
            if is_blocked: r.active_states.append("Overtake")
            if r.pos_comp_timer > 0: r.active_states.append("PosComp")
            if r.oikurabe_timer > 0: r.active_states.append("Oikurabe")

            current_frame["r"].append([
                r.id, 
                round(r.dist, 2), 
                round(r.lane, 3), 
                round(r.speed, 2), 
                int(r.hp), 
                r.active_states
            ])
            
        if current_frame["r"]:
            self.frames.append(current_frame)


@app.route('/api/create_room_final', methods=['POST'])
def create_room_final():
    # 생성 전, 오래된 방 청소 (메모리 최적화)
    cleanup_old_rooms()
    
    data = request.json
    url = data.get('url')
    participants = data.get('participants') 
    scheduled_time = data.get('scheduled_time') 
    map_type = data.get('map_type', 'short')
    bgm = data.get('bgm', 'none')
    
    if not url or not participants or not scheduled_time:
        return jsonify({"success": False, "message": "필수 데이터가 누락되었습니다."}), 400
        
    if len(participants) < 2:
        return jsonify({"success": False, "message": "최소 2명 이상의 주자가 필요합니다."}), 400
        
    room_id = generate_room_id()
    
    # 트랙 길이 설정 (short: 8000, long: 20000)
    track_len = 8000 if map_type == 'short' else 20000
    
    # 참가자 무작위 스탯 부여
    runners = []
    styles = ["도주", "선행", "선입", "추입"]
    
    for idx, p_name in enumerate(participants):
        style = random.choice(styles)
        # 스탯 난수 (프론트엔드와 유사하게 400~1200 보정)
        spd = random.randint(500, 1200)
        stam = random.randint(500, 1200)
        power = random.randint(500, 1200)
        guts = random.randint(500, 1200)
        intel = random.randint(500, 1200)
        
        start_lane = 4.0 + (idx % 8) * 1.5 # 게이트 포지션 분산
        runner = Uma(idx, p_name, style, spd, stam, power, guts, intel, track_len, start_lane)
        runners.append(runner)
        
    # 파이썬 코어 엔진 실행 (사전 연산)
    simulator = RaceSimulator(runners, track_len)
    replay_frames = simulator.run()
    
    # 룸 데이터베이스에 저장
    ROOMS_DB[room_id] = {
        "url": url,
        "participants": [{"id": r.id, "name": r.name, "style": r.style} for r in runners],
        "scheduled_time": scheduled_time,
        "map_type": map_type,
        "bgm": bgm,
        "created_at": int(time.time()),
        "replay_data": replay_frames # 프론트엔드는 이 데이터를 받아 재생합니다.
    }
    
    return jsonify({"success": True, "room_id": room_id})

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    cleanup_old_rooms() # 조회 시에도 주기적으로 청소
    room_data = ROOMS_DB.get(room_id.upper())
    if not room_data:
        return jsonify({"success": False, "message": "존재하지 않는 방이거나, 생성된 지 1시간이 지나 삭제되었습니다."}), 404
    return jsonify({"success": True, "data": room_data})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)