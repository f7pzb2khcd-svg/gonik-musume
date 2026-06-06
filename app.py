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

app = Flask(__name__)
CORS(app)

ROOMS_DB = {}

def cleanup_old_rooms():
    current_time = time.time()
    expired_rooms = [rid for rid, data in ROOMS_DB.items() if current_time - data["created_at"] > 3600]
    for rid in expired_rooms:
        del ROOMS_DB[rid]

def generate_room_id(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def extract_racers(url):
    gall_id = None
    gall_no = None
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    if 'id' in qs and 'no' in qs:
        gall_id = qs['id'][0]; gall_no = qs['no'][0]
    else:
        match = re.search(r'/(?:board|mini|mgallery)/([^/?]+)/([^/?]+)', url)
        if match: gall_id = match.group(1); gall_no = match.group(2)
            
    if not gall_id or not gall_no: return [], "URL 에러"
    target_url = url if "m.dcinside.com" not in url else f"https://gall.dcinside.com/board/view/?id={gall_id}&no={gall_no}"

    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gall.dcinside.com/"}
    session = requests.Session()
    try:
        res = session.get(target_url, headers=headers, timeout=5)
        if "location.replace" in res.text:
            match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]\)", res.text)
            if match:
                target_url = "https://gall.dcinside.com" + match.group(1) if match.group(1).startswith('/') else match.group(1)
                res = session.get(target_url, headers=headers, timeout=5)
    except: return [], "접속 오류"
        
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    t_input = soup.find('input', {'id': 'e_s_n_o'})
    if t_input: e_s_n_o = t_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match: e_s_n_o = match.group(1)

    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy()
    ajax_headers["X-Requested-With"] = "XMLHttpRequest"
    payload = {"id": gall_id, "no": gall_no, "cmt_id": gall_id, "cmt_no": gall_no, "e_s_n_o": e_s_n_o, "_GALLTYPE_": "G" if "mgallery" not in target_url else "M", "page": 1}
    
    try: cmt_data = session.post(ajax_url, data=payload, headers=ajax_headers).json()
    except: return [], "댓글 로드 실패"
        
    racers = {} 
    if not cmt_data or "comments" not in cmt_data: return [], "댓글 없음"
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict): continue
        uid = cmt.get("user_id", ""); nick = cmt.get("name", "ㅇㅇ"); reg_date = cmt.get("reg_date", "")
        if not uid: continue
        key = f"{nick}({uid})"
        if key not in racers: racers[key] = reg_date
    return [{"name": k, "reg_date": v} for k, v in racers.items()], None

@app.route('/api/extract_only', methods=['POST'])
def extract_only():
    data = request.json
    participants, error = extract_racers(data.get('url'))
    if error: return jsonify({"success": False, "message": error}), 400
    return jsonify({"success": True, "participants": participants})

# ==========================================
# 🐎 [백엔드 코어] 기획서 완벽 반영 엔진
# ==========================================
FPS = 15
DT = 1.0 / FPS

STYLE_MODS = {
    "도주": {"spd": [1.000, 0.980, 0.962], "acc": [1.000, 1.000, 0.996], "hp": 0.95},
    "선행": {"spd": [0.978, 0.991, 0.975], "acc": [0.985, 1.000, 0.996], "hp": 0.89},
    "선입": {"spd": [0.938, 0.998, 0.994], "acc": [0.975, 1.000, 1.000], "hp": 1.00},
    "추입": {"spd": [0.931, 1.000, 1.000], "acc": [0.945, 1.000, 0.997], "hp": 0.995}
}

class Uma:
    def __init__(self, uid, name, style, raw_stats, cond_mult, track_len, start_lane):
        self.id = uid; self.name = name; self.original_style = style; self.style = style 
        self.spd = raw_stats['spd'] * cond_mult; self.stam = raw_stats['stam'] * cond_mult
        self.pow = raw_stats['pow'] * cond_mult; self.guts = raw_stats['guts'] * cond_mult; self.intel = raw_stats['intel'] * cond_mult
        self.track_len = track_len
        self.max_hp = 0.8 * STYLE_MODS[style]["hp"] * self.stam + track_len; self.hp = self.max_hp
        self.dist = 0.0; self.speed = 3.0; self.lane = start_lane; self.target_speed = 3.0
        self.start_delay = math.floor(random.uniform(0, 0.1) / DT) * DT 
        
        self.section = 1; self.phase = 0; self.last_section_eval = 0
        
        self.is_start_dash = True; self.is_exhausted = False; self.is_spurting = False; self.is_kakari = False
        self.kakari_timer = 0; self.has_kakari_history = False
        
        # 기획서 모드
        self.pace_mode = "Normal"
        self.pace_mode_mod = 1.0
        self.is_overtaking = False; self.overtake_timer = 0
        self.is_evading = False; self.evade_timer = 0
        self.active_states = []

class RaceSimulator:
    def __init__(self, runners, track_len):
        self.runners = runners; self.track_len = track_len
        self.section_len = track_len / 24.0
        self.base_speed = max(16.0, 20.0 - ((track_len - 2000) / 1000.0))
        self.time = 0.0; self.frames = []; self.race_mod = 0.0008 * (track_len - 1000) + 1.0

    def run(self):
        safety = 0
        while any(r.dist < self.track_len for r in self.runners) and safety < 30000:
            self.tick(); self.time += DT; safety += 1
        return self.frames

    def tick(self):
        current_frame = {"t": round(self.time, 2), "r": []}
        leader = max(self.runners, key=lambda x: x.dist)

        for r in self.runners:
            if r.dist >= self.track_len: continue
            r.active_states = []
            if self.time < r.start_delay:
                current_frame["r"].append([r.id, 0.0, round(r.lane, 2), 0.0, int(r.hp), ["LateStart"]])
                continue

            r.section = math.floor(r.dist / self.section_len) + 1
            if r.section <= 4: r.phase = 0
            elif r.section <= 16: r.phase = 1
            else: r.phase = 2

            # 💡 [핵심] 구간 진입 시 판정 (1~10구간 포지션킵 + 전구간 추월)
            if r.section > r.last_section_eval:
                r.last_section_eval = r.section
                r.pace_mode = "Normal"
                r.pace_mode_mod = 1.0
                
                # 포지션 킵 (1~10 구간)
                if r.section <= 10:
                    is_escape = (r.style == "도주" or (leader.style != "도주" and r.id == leader.id))
                    if is_escape:
                        behind_strats = {"도주": ["선행", "선입", "추입"], "선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                        if any(o.dist > r.dist and o.style in behind_strats[r.style] for o in self.runners):
                            r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0
                        elif r.id == leader.id:
                            diff_2nd = leader.dist - max([o.dist for o in self.runners if o.id != leader.id] + [0])
                            limit = 12.5 if sum(1 for o in self.runners if o.style == "도주") == 1 else 4.5
                            if diff_2nd <= limit and random.random() < (1.0 if r.is_kakari else (20 * math.log10(r.intel * 0.1))/100.0):
                                r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04
                        elif r.is_kakari:
                            r.pace_mode = "Chase"; r.pace_mode_mod = 1.05
                    else:
                        behind_strats = {"선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                        if leader.style in behind_strats.get(r.style, []):
                            r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0
                        else:
                            dist_diff = leader.dist - r.dist
                            upper = {"선행": 5.0, "선입": 7.0, "추입": 8.0}[r.style] * self.race_mod
                            lower = {"선행": 3.0, "선입": 6.5, "추입": 7.5}[r.style] * (self.race_mod if r.style != "선행" else 1.0)
                            if dist_diff < lower:
                                r.pace_mode = "PaceDown"; r.pace_mode_mod = 0.915 if r.phase == 0 else 0.945
                            elif dist_diff >= upper and random.random() < (1.0 if r.is_kakari else (15 * math.log10(r.intel * 0.1))/100.0):
                                r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04

                # 추월 모드 판정
                if not r.is_overtaking:
                    for target in self.runners:
                        if target.id == r.id: continue
                        dist_diff = target.dist - r.dist
                        if 0 < dist_diff <= 20.0:
                            speed_diff = r.speed - target.speed
                            time_to_catch = dist_diff / speed_diff if speed_diff > 0 else 999
                            target_blocked = any(o.id != target.id and 0 < o.dist - target.dist <= 2.0 and abs(o.lane - target.lane) <= 0.5 for o in self.runners)
                            
                            if (time_to_catch < 15.0 and r.target_speed > target.target_speed) or (target_blocked and r.target_speed > target.speed):
                                if random.random() < max(100 - 9000 / r.intel, 20) / 100.0:
                                    r.is_overtaking = True; r.overtake_timer = 1.5
                                    if random.random() < max(100 - 9000 / target.intel, 20) / 100.0:
                                        target.is_evading = True; target.evade_timer = 1.5
                                    break

            # 흥분 상태
            if 2 <= r.section <= 9 and not r.is_kakari and not r.has_kakari_history:
                if random.random() < math.pow(6.5 / math.log10(0.1 * r.intel + 1), 2) / 100.0:
                    r.is_kakari = True; r.has_kakari_history = True; r.kakari_timer = 12.0
            if r.is_kakari:
                r.kakari_timer -= DT
                if r.kakari_timer % 3.0 < DT and random.random() < 0.55: r.kakari_timer = 0
                if r.kakari_timer <= 0: r.is_kakari = False; r.style = r.original_style

            # 타이머 갱신
            if r.is_overtaking:
                r.overtake_timer -= DT
                if r.overtake_timer <= 0: r.is_overtaking = False
            if r.is_evading:
                r.evade_timer -= DT
                if r.evade_timer <= 0: r.is_evading = False

            # 속도 연산
            t_mod = STYLE_MODS[r.style]["spd"][r.phase]
            base_target_speed = self.base_speed * t_mod
            spd_bonus = math.sqrt(500 * r.spd) * 0.002
            guts_bonus = math.pow(450 * r.guts, 0.597) * 0.0001
            
            if r.phase == 2: base_target_speed += spd_bonus
            spurt_target = (base_target_speed + 0.01 * self.base_speed) * 1.05 + spd_bonus + guts_bonus

            if r.hp <= 0 and not r.is_exhausted:
                r.is_exhausted = True; r.is_spurting = False
            elif r.phase == 2 and not r.is_spurting and not r.is_exhausted:
                guts_drain_mod = 1.0 + 200.0 / math.sqrt(600 * r.guts)
                req_hp = (((self.track_len - r.dist) - 60) / spurt_target) * ((20 * math.pow(spurt_target - self.base_speed + 12, 2) / 144.0) * guts_drain_mod)
                if r.hp >= req_hp: r.is_spurting = True

            # 모드 적용
            target_speed = base_target_speed * r.pace_mode_mod
            if r.is_overtaking: target_speed *= 1.05
            if r.is_evading: target_speed *= 1.04
            
            if r.is_exhausted: target_speed = 0.85 * self.base_speed + math.sqrt(200 * r.guts) * 0.001
            elif r.is_spurting: target_speed = spurt_target
            r.target_speed = target_speed

            accel = math.sqrt(500 * r.pow) * 0.002 * STYLE_MODS[r.style]["acc"][r.phase]
            if r.is_spurting and r.pow > 1000:
                accel += (0.05 + (r.pow - 1000) * 0.00015) * (0.8 if r.has_kakari_history else 1.0)
            if r.is_start_dash:
                if r.speed < self.base_speed * 0.85: accel += 24.0
                else: r.is_start_dash = False

            decel = 1.2 if r.is_exhausted else (0.5 if r.pace_mode == "PaceDown" else (1.0 if r.phase == 2 else 0.8))

            if r.speed < target_speed: r.speed = min(r.speed + accel * DT, target_speed)
            elif r.speed > target_speed: r.speed = max(r.speed - decel * DT, target_speed)

            if not r.is_exhausted:
                hp_drain = 20 * math.pow(r.speed - self.base_speed + 12, 2) / 144.0
                hp_drain *= (1.6 if r.is_kakari else (0.6 if r.pace_mode == "PaceDown" else 1.0))
                if r.phase == 2: hp_drain *= (1.0 + 200.0 / math.sqrt(600 * r.guts))
                r.hp = max(0, r.hp - hp_drain * DT)

            r.dist += r.speed * DT
            target_lane = max(0.0, min((r.lane + 0.28) if r.pace_mode == "PaceDown" else (r.lane - 0.08), 28.8))
            lane_move_speed = 0.02 * (0.3 + 0.001 * r.pow) * 1.5 * DT * 60
            if r.lane < target_lane: r.lane = min(r.lane + lane_move_speed, target_lane)
            elif r.lane > target_lane: r.lane = max(r.lane - lane_move_speed, target_lane)

            if r.is_start_dash: r.active_states.append("Dash")
            if r.is_spurting: r.active_states.append("Spurt")
            if r.is_exhausted: r.active_states.append("Exhaust")
            if r.is_kakari: r.active_states.append("Kakari")
            if r.pace_mode != "Normal": r.active_states.append(r.pace_mode)
            if r.is_overtaking: r.active_states.append("Overtake")

            current_frame["r"].append([r.id, round(r.dist, 2), round(r.lane, 2), round(r.speed, 2), int(r.hp), r.active_states])
        if current_frame["r"]: self.frames.append(current_frame)

@app.route('/api/create_room_final', methods=['POST'])
def create_room_final():
    cleanup_old_rooms(); data = request.json
    if not data.get('url') or not data.get('participants') or not data.get('scheduled_time'):
        return jsonify({"success": False, "message": "데이터 누락"}), 400
        
    room_id = generate_room_id(); track_len = 2000; runners = []
    STAT_WEIGHTS = {
        "도주": {'spd': 0.32, 'stam': 0.23, 'pow': 0.15, 'guts': 0.10, 'intel': 0.20},
        "선행": {'spd': 0.30, 'stam': 0.22, 'pow': 0.18, 'guts': 0.12, 'intel': 0.18},
        "선입": {'spd': 0.28, 'stam': 0.18, 'pow': 0.25, 'guts': 0.12, 'intel': 0.17},
        "추입": {'spd': 0.30, 'stam': 0.15, 'pow': 0.28, 'guts': 0.12, 'intel': 0.15}
    }
    
    for idx, p_name in enumerate(data['participants']):
        style = random.choice(["도주", "선행", "선입", "추입"])
        raw_stats = {'spd': 300, 'stam': 300, 'pow': 300, 'guts': 300, 'intel': 300}
        for k in raw_stats.keys(): raw_stats[k] += int(2500 * STAT_WEIGHTS[style][k])
        for _ in range(15):
            k1, k2 = random.sample(list(raw_stats.keys()), 2)
            if raw_stats[k1] < 1200 and raw_stats[k2] > 300:
                trans = min(random.randint(1, 20), 1200 - raw_stats[k1], raw_stats[k2] - 300)
                if trans > 0: raw_stats[k1] += trans; raw_stats[k2] -= trans
        
        runner = Uma(idx, p_name, style, raw_stats, random.choice([1.05, 1.02, 1.00, 0.98, 0.95]), track_len, 4.0 + (idx % 8) * 1.5)
        runners.append(runner)
        
    ROOMS_DB[room_id] = {
        "url": data['url'], "participants": [{"id": r.id, "name": r.name, "style": r.style} for r in runners],
        "replay_data": RaceSimulator(runners, track_len).run(), "created_at": time.time(),
        "scheduled_time": data['scheduled_time'], "bgm": data.get('bgm', 'none'), "allow_custom_chat": data.get('allow_custom_chat', False)
    }
    return jsonify({"success": True, "room_id": room_id})

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    cleanup_old_rooms()
    return jsonify({"success": True, "data": ROOMS_DB[room_id.upper()]}) if room_id.upper() in ROOMS_DB else jsonify({"success": False, "message": "방 없음"}), 404

if __name__ == '__main__': app.run(host='0.0.0.0', port=5000, debug=True)