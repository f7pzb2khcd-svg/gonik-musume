import csv
import re
import string
import random
import time
import math
import os
from collections import deque
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import urllib.parse

app = Flask(__name__)
CORS(app)

ROOMS_DB = {}

def cleanup_old_rooms():
    current_time = time.time()
    expired_rooms = [rid for rid, data in ROOMS_DB.items() if current_time - data["created_at"] > 3600]
    for rid in expired_rooms: del ROOMS_DB[rid]

def generate_room_id(length=6):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choice(characters) for _ in range(length))

def extract_racers(url):
    gall_id = None; gall_no = None
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    
    if 'id' in qs and 'no' in qs:
        gall_id = qs['id'][0]; gall_no = qs['no'][0]
    else:
        match = re.search(r'/(?:board|mini|mgallery)/([^/?]+)/([^/?]+)', url)
        if match: gall_id = match.group(1); gall_no = match.group(2)
            
    if not gall_id or not gall_no: return [], "URL 에러"

    target_url = url if "m.dcinside.com" not in url else f"https://gall.dcinside.com/board/view/?id={gall_id}&no={gall_no}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://gall.dcinside.com/"}
    session = requests.Session()
    
    try:
        res = session.get(target_url, headers=headers, timeout=5)
        res.raise_for_status()
        if "location.replace" in res.text:
            redirect_match = re.search(r"location\.replace\(['\"]([^'\"]+)['\"]\)", res.text)
            if redirect_match:
                target_url = redirect_match.group(1)
                if target_url.startswith('/'): target_url = "https://gall.dcinside.com" + target_url
                res = session.get(target_url, headers=headers, timeout=5)
                res.raise_for_status()
    except Exception as e: return [], f"게시글 접속 오류: {e}"
        
    e_s_n_o = ""
    soup = BeautifulSoup(res.text, 'html.parser')
    token_input = soup.find('input', {'id': 'e_s_n_o'})
    if token_input: e_s_n_o = token_input.get('value')
    else:
        match = re.search(r'var\s+e_s_n_o\s*=\s*"([^"]+)"', res.text)
        if match: e_s_n_o = match.group(1)

    gall_type = "G"
    if "mgallery" in target_url or "mgallery" in res.url: gall_type = "M"
    elif "mini" in target_url or "mini" in res.url: gall_type = "MI"

    ajax_url = "https://gall.dcinside.com/board/comment/"
    ajax_headers = headers.copy(); ajax_headers["X-Requested-With"] = "XMLHttpRequest"; ajax_headers["Referer"] = res.url
    payload = {"id": gall_id, "no": gall_no, "cmt_id": gall_id, "cmt_no": gall_no, "e_s_n_o": e_s_n_o, "_GALLTYPE_": gall_type, "page": 1}
    
    try: cmt_data = session.post(ajax_url, data=payload, headers=ajax_headers).json()
    except Exception as e: return [], f"댓글 API 실패: {e}"
        
    racers = {} 
    if not cmt_data or "comments" not in cmt_data or not cmt_data["comments"]: return [], "댓글 없음"
        
    for cmt in cmt_data["comments"]:
        if not isinstance(cmt, dict): continue
        uid = cmt.get("user_id", ""); nick = cmt.get("name", "ㅇㅇ"); reg_date = cmt.get("reg_date", "")
        if not uid: continue
        user_key = f"{nick}({uid})"
        if user_key not in racers: racers[user_key] = reg_date
            
    return [{"name": k, "reg_date": v} for k, v in racers.items()], None

@app.route('/api/extract_only', methods=['POST'])
def extract_only():
    data = request.json
    participants, error = extract_racers(data.get('url'))
    if error: return jsonify({"success": False, "message": error}), 400
    return jsonify({"success": True, "participants": participants})

SKILL_DB = {}
                                                                                                        
try:
    with open('skills.csv', 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or not row[0].strip(): continue
            if len(row) < 16: row.extend([''] * (16 - len(row)))
            s_id = row[0].strip()
            SKILL_DB[s_id] = {
                "id": s_id, "name": row[1].strip(), "icon": row[2].strip(),
                "conditions": [c for c in row[3:7] if c.strip()],
                "effects": []
            }
            for i in range(7, 16, 3):
                if row[i].strip():
                    target = "self"
                    eff_type = row[i].strip()
                    if "근처에 있는 주자" in eff_type: target = "nearby"; eff_type = eff_type.replace("근처에 있는 주자 ", "")
                    elif "앞에 있는 주자" in eff_type: target = "front"; eff_type = eff_type.replace("앞에 있는 주자 ", "")
                    elif "뒤에 있는 주자" in eff_type: target = "behind"; eff_type = eff_type.replace("뒤에 있는 주자 ", "")
                    elif "옆에 있는 주자" in eff_type: target = "side"; eff_type = eff_type.replace("옆에 있는 주자 ", "")
                    elif "나를 제외한 주자" in eff_type: target = "all_enemies"; eff_type = eff_type.replace("나를 제외한 주자 ", "")
                    
                    if eff_type == "속도 증가": eff_type = "목표 속도 증가"
                    if eff_type == "속도 감소": eff_type = "목표 속도 감소"

                    val = float(row[i+1]) if row[i+1].strip() else 0.0
                    dur = float(row[i+2]) if row[i+2].strip() else 0.0
                    SKILL_DB[s_id]["effects"].append({"target": target, "type": eff_type, "val": val, "dur": dur})
except Exception as e:
    print("skills.csv 파일 로드 실패:", e)

ULT_DB = {}

try:
    with open('ultimates.csv', 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or not row[0].strip(): continue
            if len(row) < 15: row.extend([''] * (15 - len(row)))
            u_id = row[0].strip()
            ULT_DB[u_id] = {
                "id": u_id, "name": row[1].strip(), "sound": row[2].strip(),
                "conditions": [c.strip() for c in row[3:6] if c.strip()],
                "effects": []
            }
            for i in range(6, 15, 3):
                if row[i].strip():
                    eff_type = row[i].strip()
                    target = "self"
                    if "근처에 있는 주자" in eff_type: target = "nearby"; eff_type = eff_type.replace("근처에 있는 주자 ", "")
                    elif "앞에 있는 주자" in eff_type: target = "front"; eff_type = eff_type.replace("앞에 있는 주자 ", "")
                    elif "뒤에 있는 주자" in eff_type: target = "behind"; eff_type = eff_type.replace("뒤에 있는 주자 ", "")
                    elif "옆에 있는 주자" in eff_type: target = "side"; eff_type = eff_type.replace("옆에 있는 주자 ", "")
                    elif "나를 제외한 주자" in eff_type: target = "all_enemies"; eff_type = eff_type.replace("나를 제외한 주자 ", "")
                    
                    if eff_type == "속도 증가": eff_type = "목표 속도 증가"
                    if eff_type == "속도 감소": eff_type = "목표 속도 감소"

                    val = float(row[i+1]) if row[i+1].strip() else 0.0
                    dur = float(row[i+2]) if row[i+2].strip() else 0.0
                    ULT_DB[u_id]["effects"].append({"target": target, "type": eff_type, "val": val, "dur": dur})
except Exception as e:
    print("ultimates.csv 파일 로드 실패:", e)

FPS = 15
DT = 1.0 / FPS

TRACK_SEGMENTS = [
    (0, 200, "straight"), (200, 400, "corner"), (400, 950, "straight"),
    (950, 1225, "corner"), (1225, 1475, "corner"), (1475, 2000, "straight")
]

STYLE_MODS = {
    "도주": {"spd": [1.000, 0.980, 0.962], "acc": [1.000, 1.000, 0.996], "hp": 0.95},
    "선행": {"spd": [0.978, 0.991, 0.975], "acc": [0.985, 1.000, 0.996], "hp": 0.89},
    "선입": {"spd": [0.938, 0.998, 0.994], "acc": [0.975, 1.000, 1.000], "hp": 1.00},
    "추입": {"spd": [0.931, 1.000, 1.000], "acc": [0.945, 1.000, 0.997], "hp": 0.995}
}

class Uma:
    def __init__(self, uid, name, style, raw_stats, cond_mult, track_len, start_lane, start_dist):
        self.id = uid; self.name = name; self.original_style = style; self.style = style 
        self.base_spd = raw_stats['spd'] * cond_mult; self.spd = self.base_spd
        self.base_stam = raw_stats['stam'] * cond_mult; self.stam = self.base_stam
        self.base_pow = raw_stats['pow'] * cond_mult; self.pow = self.base_pow
        self.base_guts = raw_stats['guts'] * cond_mult; self.guts = self.base_guts
        self.intel = raw_stats['intel'] * cond_mult
        
        self.track_len = track_len
        self.max_hp = 0.8 * STYLE_MODS[style]["hp"] * self.stam + track_len; self.hp = self.max_hp
        self.dist = start_dist; self.speed = 3.0; self.lane = start_lane; self.target_speed = 3.0
        self.target_lane = start_lane
        
        self.start_delay = math.floor(random.uniform(0, 0.1) / DT) * DT 
        self.section = 1; self.phase = 0; self.rank = 1
        self.is_start_dash = True; self.is_exhausted = False; self.is_spurting = False
        
        intel_bonus = max(self.intel, 1) * 0.1
        kakari_prob = math.pow(6.5 / math.log10(intel_bonus + 1), 2) / 100.0
        self.is_kakari_planned = random.random() < kakari_prob
        self.kakari_target_section = random.randint(2, 9) if self.is_kakari_planned else 0
        self.is_kakari = False; self.kakari_timer = 0; self.has_kakari_history = False
        
        self.pace_mode = "Normal"; self.pace_mode_mod = 1.0; self.pace_eval_cd = 0.0
        self.pacedown_target_set = False; self.pace_activation_section = 0; self.pace_target_dist = 0.0
        self.is_pace_eval_passed = set() 
        
        self.is_overtaking = False; self.overtake_timer = 0; self.overtake_target = None; self.overtake_target_lane = None 
        self.is_evading = False; self.evade_timer = 0; self.overtake_eval_cd = 0.0
        
        self.is_blocked = False; self.is_contested = False; self.is_followed = False; self.is_nearby = False
        self.time_blocked = 0.0; self.time_contested = 0.0; self.time_followed = 0.0; self.time_in_final_straight = 0.0
        
        self.bump_cd = 0.0 
        self.stam_sys_cd = 0.0; self.stam_sys_active = None; self.stam_sys_timer = 0.0
        self.active_states = []
        
        self.owned_skills = []   
        self.active_skills = []  
        self.skill_mod_target_speed = 0.0
        self.skill_mod_accel = 0.0
        self.pending_ult = None
        self.pending_popups = []
        self.ult_part_timer = 0.0
        self.ult_part_dir = 0.0
        self.ult_part_origin = 0.0

class RaceSimulator:
    def __init__(self, runners, track_len):
        self.runners = runners; self.track_len = track_len
        self.section_len = track_len / 24.0
        self.base_speed = max(16.0, 20.0 - ((track_len - 2000) / 1000.0))
        self.time = 0.0; self.frames = []; self.race_mod = 0.0008 * (track_len - 1000) + 1.0
        self.start_phase_cleared = False
        self.ult_triggered = False
        
        UNIQUE_SKILLS = [s for s in SKILL_DB.values() if s['icon'] == '고유.png']
        COMMON_SKILLS = [s for s in SKILL_DB.values() if s['icon'] != '고유.png']
        
        for r in self.runners:
            unique_count = 0
            if r.intel >= 1000: unique_count = 4
            elif r.intel >= 900: unique_count = 3
            elif r.intel >= 825: unique_count = 2
            elif r.intel >= 750: unique_count = 1
            
            selected_uniques = random.sample(UNIQUE_SKILLS, min(unique_count, len(UNIQUE_SKILLS)))
            selected_commons = random.sample(COMMON_SKILLS, min(8, len(COMMON_SKILLS)))
            candidate_skills = selected_uniques + selected_commons
            
            for skill_data in candidate_skills:
                prob = (20 * math.log10(max(r.intel, 1) * 0.1)) / 100.0
                if random.random() < prob:
                    cond_text = " ".join(skill_data["conditions"])
                    min_dist = 0.0; max_dist = self.track_len
                    sec_len = self.section_len
                    
                    if "초반" in cond_text: max_dist = min(max_dist, self.track_len / 6.0)
                    if "중반 이후" in cond_text: min_dist = max(min_dist, self.track_len / 6.0)
                    elif "중반" in cond_text: min_dist = max(min_dist, self.track_len / 6.0); max_dist = min(max_dist, self.track_len * (16.0 / 24.0))
                    if "종반" in cond_text: min_dist = max(min_dist, self.track_len * (16.0 / 24.0))
                    if "10구간 이전" in cond_text: max_dist = min(max_dist, 10.0 * sec_len)
                    
                    match_after = re.search(r'(\d+)구간 이후', cond_text)
                    if match_after: min_dist = max(min_dist, (int(match_after.group(1)) - 1) * sec_len)
                    
                    match_range = re.search(r'(\d+)구간~(\d+)구간 무작위', cond_text)
                    if match_range:
                        min_dist = max(min_dist, (int(match_range.group(1)) - 1) * sec_len)
                        max_dist = min(max_dist, int(match_range.group(2)) * sec_len)
                        
                    is_corner = "무작위 코너" in cond_text
                    is_straight = "무작위 직선" in cond_text

                    valid_ranges = []
                    if is_corner or is_straight:
                        for s, e, t in TRACK_SEGMENTS:
                            s_scaled = s * (self.track_len / 2000.0)
                            e_scaled = e * (self.track_len / 2000.0)
                            if is_corner and t != "corner": continue
                            if is_straight and t != "straight": continue
                            ns = max(s_scaled, min_dist)
                            ne = min(e_scaled, max_dist)
                            if ns < ne: valid_ranges.append((ns, ne))
                    else:
                        if min_dist < max_dist: valid_ranges.append((min_dist, max_dist))

                    if "무작위" in cond_text and "진입" not in cond_text and valid_ranges:
                        chosen = random.choice(valid_ranges)
                        target_dist = random.uniform(chosen[0], chosen[1])
                    else:
                        target_dist = -1
                        
                    r.owned_skills.append({
                        "id": skill_data["id"], "data": skill_data,
                        "triggered": False, "target_dist": target_dist
                    })

    def run(self):
        safety = 0
        first_finish_time = None
        while safety < 30000:
            self.tick()
            self.time += DT
            safety += 1
            leader_dist = max(r.dist for r in self.runners)
            if leader_dist >= self.track_len and first_finish_time is None:
                first_finish_time = self.time
            if first_finish_time is not None and self.time >= first_finish_time + 3.0:
                break
            if all(r.is_exhausted and r.speed < 1.0 for r in self.runners) and safety > 1000:
                break
        return self.frames

    def get_cluster_bounds(self, center_runner):
        visited = set([center_runner.id])
        queue = deque([center_runner])
        min_lane = center_runner.lane; max_lane = center_runner.lane
        while queue:
            curr = queue.popleft()
            for o in self.runners:
                if o.id in visited or o.bump_cd > 0: continue 
                if abs(o.dist - curr.dist) <= 3.0 and abs(o.lane - curr.lane) <= (2 * 0.08):
                    visited.add(o.id); queue.append(o)
                    if o.lane < min_lane: min_lane = o.lane
                    if o.lane > max_lane: max_lane = o.lane
        return min_lane, max_lane

    def get_pacemaker(self):
        for style in ["도주", "선행", "선입", "추입"]:
            group = [r for r in self.runners if r.style == style]
            if group: return max(group, key=lambda x: x.dist)
        return max(self.runners, key=lambda x: x.dist)

    def check_skill_conditions(self, r, skill):
        if skill["target_dist"] >= 0 and r.dist < skill["target_dist"]: return False
        data = skill["data"]
        for c in data["conditions"]:
            if "초반" in c and r.phase != 0: return False
            if "중반 이후" in c and r.phase < 1: return False
            elif "중반" in c and r.phase != 1: return False
            if "종반" in c and r.phase != 2: return False
            match = re.search(r'(\d+)구간 이후', c)
            if match and r.section < int(match.group(1)): return False
            match = re.search(r'(\d+)구간 이전', c)
            if match and r.section > int(match.group(1)): return False
            match = re.search(r'(\d+)구간 진입', c)
            if match and r.section != int(match.group(1)): return False
            
            scale = self.track_len / 2000.0
            is_corner = any(s * scale <= r.dist <= e * scale for s, e, t in TRACK_SEGMENTS if t == "corner")
            is_straight = not is_corner
            is_final_corner = (1225 * scale <= r.dist <= 1475 * scale)
            is_final_straight = (r.dist > 1475 * scale)
            
            if "코너 진입" in c and "최종" not in c and not (is_corner and r.dist - r.speed * DT * 15 < 200 * scale): return False
            if "최종 코너 진입" in c and not (is_final_corner and r.dist - r.speed * DT * 15 < 1225 * scale): return False
            if "최종 직선 진입" in c and not (is_final_straight and r.dist - r.speed * DT * 15 < 1475 * scale): return False
            if "직선 진입" in c and "최종" not in c and not (is_straight and r.dist - r.speed * DT * 15 < 400 * scale): return False
            if "무작위 코너" in c and not is_corner: return False
            if "무작위 직선" in c and not is_straight: return False
            if "최종 코너" in c and "진입" not in c and not is_final_corner: return False
            if "최종 직선" in c and "진입" not in c and not is_final_straight: return False
            
            if "근처에 주자" in c:
                if "둘 이상" in c:
                    nearby_count = sum(1 for o in self.runners if o.id != r.id and abs(o.dist - r.dist) <= 3.0 and abs(o.lane - r.lane) <= (3.0 * 0.08))
                    if nearby_count < 2: return False
                elif not r.is_nearby: return False
            if "앞이 가로막힘" in c:
                if "2초" in c and r.time_blocked < 2.0: return False
                elif r.time_blocked < 0.1: return False
            if "양 옆이 가로막힘" in c:
                if r.time_contested < 0.1: return False
            if "옆이 가로막힘" in c:
                if "5초" in c and r.time_contested < 5.0: return False
                elif "2초" in c and r.time_contested < 2.0: return False
                elif r.time_contested < 0.1: return False
            if "바로 뒤에 주자" in c:
                if "2초" in c and r.time_followed < 2.0: return False
                elif r.time_followed < 0.1: return False
            if "둘러싸임" in c:
                if not (r.is_blocked and r.is_contested and r.is_followed): return False
            if "앞이 가로막히지 않음" in c and r.is_blocked: return False
            if "최종 직선 진입 후 5초 이상" in c and r.time_in_final_straight < 5.0: return False
            if "레인 0.4 이내" in c and r.lane > 0.4: return False
            if "추월모드" in c and not r.is_overtaking: return False
            if "순위" in c:
                rank_pct = r.rank / max(len(self.runners), 1)
                match = re.search(r'(\d+)%', c)
                if match:
                    val = int(match.group(1)) / 100.0
                    if "이하" in c and rank_pct > val: return False 
                    if "이상" in c and rank_pct < val: return False 
                    if "~" in c:
                        match2 = re.findall(r'(\d+)%', c)
                        if len(match2) == 2:
                            v1, v2 = int(match2[0])/100.0, int(match2[1])/100.0
                            if not (v1 <= rank_pct <= v2): return False
                if "위 이내" in c:
                    match = re.search(r'(\d+)위', c)
                    if match and r.rank > int(match.group(1)): return False
        return True

    def tick(self):
        current_frame = {"t": round(self.time, 2), "r": []}
        
        sorted_runners = sorted(self.runners, key=lambda x: x.dist, reverse=True)
        for i, r in enumerate(sorted_runners): r.rank = i + 1
            
        pacemaker = None
        if self.time >= 2.0 or any(r.dist >= self.section_len for r in self.runners): self.start_phase_cleared = True
        if self.start_phase_cleared: pacemaker = self.get_pacemaker()

        if pacemaker and pacemaker.section >= 21 and not self.ult_triggered:
            self.ult_triggered = True
            alive_runners = [r for r in self.runners if not r.is_exhausted]
            if alive_runners:
                random.shuffle(alive_runners)
                ult_user = None
                chosen_ult = None
                for r in alive_runners:
                    valid_ults = []
                    for u in ULT_DB.values():
                        mock_skill = {"target_dist": -1, "data": {"conditions": u["conditions"]}}
                        if self.check_skill_conditions(r, mock_skill):
                            valid_ults.append(u)
                    if valid_ults:
                        ult_user = r
                        chosen_ult = random.choice(valid_ults)
                        break
                
                if not ult_user:
                    ult_user = random.choice(alive_runners)
                    chosen_ult = random.choice(list(ULT_DB.values())) if ULT_DB else {"name": "테스트 필살기", "sound": "ult_test.mp3", "effects": [{"target": "self", "type": "목표 속도 증가", "val": 0.5, "dur": 6.0}]}

                if chosen_ult:
                    # 필살기 좌우 벌림 효과: 앞에 있는 주자를 살짝만 비켜나게 (최대 0.3레인)
                    runners_ahead = [o for o in self.runners if o.id != ult_user.id and o.dist > ult_user.dist]
                    for o in runners_ahead:
                        part_dir = 1.0 if o.lane >= ult_user.lane else -1.0
                        o.ult_part_timer = random.uniform(1.2, 2.0)
                        o.ult_part_dir = part_dir
                        o.ult_part_origin = o.lane  # 원래 레인 기록 (최대 변위 제한용)

                    for seq, eff in enumerate(chosen_ult["effects"]):
                        targets = []
                        if eff["target"] == "self": targets = [ult_user]
                        elif eff["target"] == "nearby": targets = [o for o in self.runners if o.id != ult_user.id and abs(o.dist - ult_user.dist) <= 3.0 and abs(o.lane - ult_user.lane) <= (3.0 * 0.08)]
                        elif eff["target"] == "front": targets = [o for o in self.runners if o.id != ult_user.id and 0 < o.dist - ult_user.dist <= 3.0]
                        elif eff["target"] == "behind": targets = [o for o in self.runners if o.id != ult_user.id and 0 < ult_user.dist - o.dist <= 3.0]
                        elif eff["target"] == "side": targets = [o for o in self.runners if o.id != ult_user.id and abs(o.dist - ult_user.dist) <= 1.0 and abs(o.lane - ult_user.lane) <= (2.0 * 0.08)]
                        elif eff["target"] == "all_enemies": targets = [o for o in self.runners if o.id != ult_user.id]

                        for t in targets:
                            t.pending_popups.append(f"POPUP|{eff['type']}|{seq}")
                            if eff["type"] == "지구력 회복": t.hp = min(t.hp + t.max_hp * (eff["val"]/100.0), t.max_hp)
                            elif eff["type"] == "지구력 감소": t.hp = max(0, t.hp + t.max_hp * (eff["val"]/100.0))
                            elif eff["type"] == "현재 속도 증가": t.speed += eff["val"]
                            elif eff["type"] in ["목표 속도 증가", "목표 속도 감소", "가속도 증가", "가속도 감소", "파워 증가", "파워 감소", "근성 증가", "근성 감소"]:
                                t.active_skills.append({"type": eff["type"], "val": eff["val"], "dur": eff["dur"]})
                    
                    ult_user.pending_ult = f"ULT|{chosen_ult['name']}|{chosen_ult['sound']}"

        for r in self.runners:
            r.active_states = []
            
            if getattr(r, 'pending_ult', None):
                r.active_states.append(r.pending_ult)
                r.pending_ult = None
                
            if getattr(r, 'pending_popups', None):
                r.active_states.extend(r.pending_popups)
                r.pending_popups = []

            contesting_runners = []
            closest_blocker = None
            stam_speed_mod = 0.0
                
            if r.dist >= self.track_len:
                target_speed = self.base_speed * (0.4 + (r.id % 5) * 0.02)
                r.is_spurting = False
                r.pace_mode = "Normal"
                r.is_overtaking = False
                r.is_blocked = False
                r.is_contested = False
                r.is_followed = False
                r.is_nearby = False
            else:
                if self.time < r.start_delay:
                    current_frame["r"].append([r.id, round(r.dist, 2), round(r.lane, 2), 0.0, int(r.hp), ["LateStart"]])
                    continue

                r.section = max(1, math.floor(r.dist / self.section_len) + 1)
                if r.section <= 4: r.phase = 0
                elif r.section <= 16: r.phase = 1
                else: r.phase = 2

                r.is_blocked = False; r.is_contested = False; r.is_followed = False; r.is_nearby = False
                min_block_dist = 999.0

                for o in self.runners:
                    if o.id == r.id: continue
                    dist_diff = o.dist - r.dist; lane_diff = abs(o.lane - r.lane)
                    if 0 < dist_diff <= 2.0 and lane_diff <= (0.5 * 0.08):
                        r.is_blocked = True
                        if dist_diff < min_block_dist: min_block_dist = dist_diff; closest_blocker = o
                    if abs(dist_diff) <= 1.0 and lane_diff <= (2.0 * 0.08): r.is_contested = True; contesting_runners.append(o)
                    if 0 < -dist_diff <= 2.5 and lane_diff <= (1.0 * 0.08): r.is_followed = True
                    if abs(dist_diff) <= 3.0 and lane_diff <= (3.0 * 0.08): r.is_nearby = True

                if r.is_blocked: r.time_blocked += DT
                else: r.time_blocked = 0
                if r.is_contested: r.time_contested += DT
                else: r.time_contested = 0
                if r.is_followed: r.time_followed += DT
                else: r.time_followed = 0
                if r.dist > 1475: r.time_in_final_straight += DT

                r.pow = r.base_pow; r.guts = r.base_guts
                r.skill_mod_target_speed = 0.0; r.skill_mod_accel = 0.0
                
                for skill in r.owned_skills:
                    if not skill["triggered"]:
                        if self.check_skill_conditions(r, skill):
                            skill["triggered"] = True
                            r.active_states.append(f"SKILL|{skill['data']['name']}|{skill['data']['icon']}")

                            # 고유 스킬 발동 시: 바로 앞 주자들에게 너프 버전 벌림 (최대 0.15레인, 짧게)
                            if skill["data"]["icon"] == "고유.png":
                                nearby_ahead = [o for o in self.runners if o.id != r.id and 0 < o.dist - r.dist <= 4.0]
                                for o in nearby_ahead:
                                    if o.ult_part_timer <= 0:  # 이미 벌림 중이면 덮어쓰지 않음
                                        o.ult_part_dir = 1.0 if o.lane >= r.lane else -1.0
                                        o.ult_part_timer = random.uniform(0.6, 1.0)
                                        o.ult_part_origin = o.lane
                                        o._unique_part_max = 0.15  # 고유 스킬은 최대 0.15레인

                            for seq, eff in enumerate(skill["data"]["effects"]):
                                targets = []
                                if eff["target"] == "self": targets = [r]
                                elif eff["target"] == "nearby": targets = [o for o in self.runners if o.id != r.id and abs(o.dist - r.dist) <= 3.0 and abs(o.lane - r.lane) <= (3.0 * 0.08)]
                                elif eff["target"] == "front": targets = [o for o in self.runners if o.id != r.id and 0 < o.dist - r.dist <= 3.0]
                                elif eff["target"] == "behind": targets = [o for o in self.runners if o.id != r.id and 0 < r.dist - o.dist <= 3.0]
                                elif eff["target"] == "side": targets = [o for o in self.runners if o.id != r.id and abs(o.dist - r.dist) <= 1.0 and abs(o.lane - r.lane) <= (2.0 * 0.08)]
                                elif eff["target"] == "all_enemies": targets = [o for o in self.runners if o.id != r.id]

                                for t in targets:
                                    t.pending_popups.append(f"POPUP|{eff['type']}|{seq}")
                                    if eff["type"] == "지구력 회복": t.hp = min(t.hp + t.max_hp * (eff["val"]/100.0), t.max_hp)
                                    elif eff["type"] == "지구력 감소": t.hp = max(0, t.hp + t.max_hp * (eff["val"]/100.0))
                                    elif eff["type"] == "현재 속도 증가": t.speed += eff["val"]
                                    else: t.active_skills.append({"type": eff["type"], "val": eff["val"], "dur": eff["dur"]})

                alive_skills = []
                has_buff = False
                has_debuff = False
                
                for askill in r.active_skills:
                    if askill["type"] == "목표 속도 증가" or askill["type"] == "목표 속도 감소": r.skill_mod_target_speed += askill["val"]
                    elif askill["type"] == "가속도 증가" or askill["type"] == "가속도 감소": r.skill_mod_accel += askill["val"]
                    elif askill["type"] == "파워 증가" or askill["type"] == "파워 감소": r.pow += askill["val"]
                    elif askill["type"] == "근성 증가" or askill["type"] == "근성 감소": r.guts += askill["val"]
                    
                    if "증가" in askill["type"] or "회복" in askill["type"]: has_buff = True
                    elif "감소" in askill["type"]: has_debuff = True
                        
                    askill["dur"] -= DT
                    if askill["dur"] > 0: alive_skills.append(askill)
                r.active_skills = alive_skills

                if has_buff: r.active_states.append("AuraBuff")
                if has_debuff: r.active_states.append("AuraDebuff")

                if r.section == r.kakari_target_section and not r.has_kakari_history:
                    r.is_kakari = True; r.has_kakari_history = True; r.kakari_timer = 12.0
                if r.is_kakari:
                    r.kakari_timer -= DT
                    if r.kakari_timer % 3.0 < DT and random.random() < 0.55: r.kakari_timer = 0
                    if r.kakari_timer <= 0: r.is_kakari = False; r.style = r.original_style

                if r.pace_eval_cd > 0: r.pace_eval_cd -= DT
                if r.section > 10 and r.pace_mode != "Normal": r.pace_mode = "Normal"; r.pace_mode_mod = 1.0; r.pace_eval_cd = 0.0
                    
                if r.pace_mode != "Normal" and pacemaker:
                    terminate = False
                    behind_strats = {"도주": ["선행", "선입", "추입"], "선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                    has_behind_enemy = any(o.dist > r.dist and o.style in behind_strats.get(r.style, []) for o in self.runners)
                    dist_to_pm = pacemaker.dist - r.dist
                    
                    if r.pace_mode == "PaceUpEx":
                        if not has_behind_enemy or r.section > r.pace_activation_section: terminate = True
                    elif r.pace_mode == "PaceUp":
                        if r.style == "도주":
                            dist_to_closest_behind = min([r.dist - o.dist for o in self.runners if o.dist < r.dist] + [999])
                            if dist_to_closest_behind >= 4.5: terminate = True
                        else:
                            if dist_to_pm <= r.pace_target_dist: terminate = True
                    elif r.pace_mode == "Chase":
                        if (r.dist - pacemaker.dist) >= 10.0 or r.section > r.pace_activation_section: terminate = True
                    elif r.pace_mode == "PaceDown":
                        if dist_to_pm <= r.pace_target_dist: terminate = True

                    if terminate: r.pace_mode = "Normal"; r.pace_mode_mod = 1.0; r.pace_eval_cd = 1.0 

                if pacemaker and r.section <= 10 and r.pace_eval_cd <= 0 and r.pace_mode == "Normal":
                    if r.section not in r.is_pace_eval_passed:
                        r.is_pace_eval_passed.add(r.section)
                        is_escape = (r.style == "도주" or (pacemaker.style != "도주" and r.id == pacemaker.id))
                        activated = False
                        if is_escape:
                            behind_strats = {"도주": ["선행", "선입", "추입"], "선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                            if any(o.dist > r.dist and o.style in behind_strats[r.style] for o in self.runners):
                                r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0; activated = True
                            elif r.id == pacemaker.id:
                                diff_2nd = pacemaker.dist - max([o.dist for o in self.runners if o.id != pacemaker.id] + [0])
                                limit = 12.5 if sum(1 for o in self.runners if o.style == "도주") == 1 else 4.5
                                if diff_2nd <= limit:
                                    if random.random() < (1.0 if r.is_kakari else (20 * math.log10(max(r.intel, 1) * 0.1))/100.0): r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04; activated = True
                                    else: r.pace_eval_cd = 2.0
                            elif r.id != pacemaker.id: 
                                if random.random() < (1.0 if r.is_kakari else (20 * math.log10(max(r.intel, 1) * 0.1))/100.0): r.pace_mode = "Chase"; r.pace_mode_mod = 1.05; activated = True
                                else: r.pace_eval_cd = 2.0
                        else:
                            behind_strats = {"선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                            if any(o.dist > r.dist and o.style in behind_strats.get(r.style, []) for o in self.runners):
                                r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0; activated = True
                            else:
                                dist_diff = pacemaker.dist - r.dist
                                upper = {"선행": 5.0, "선입": 7.0, "추입": 8.0}.get(r.style, 999) * self.race_mod
                                lower = {"선행": 3.0, "선입": 6.5, "추입": 7.5}.get(r.style, 0) * (self.race_mod if r.style != "선행" else 1.0)
                                if dist_diff < lower:
                                    r.pace_mode = "PaceDown"; r.pace_mode_mod = 0.915 if r.phase == 0 else 0.945; activated = True
                                    r.pace_target_dist = random.uniform(lower, upper)
                                elif dist_diff >= upper:
                                    if random.random() < (1.0 if r.is_kakari else (15 * math.log10(max(r.intel, 1) * 0.1))/100.0): r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04; activated = True; r.pace_target_dist = random.uniform(lower, upper)
                                    else: r.pace_eval_cd = 2.0
                        if activated: r.pace_activation_section = r.section

                if r.overtake_eval_cd > 0: r.overtake_eval_cd -= DT
                if r.is_overtaking:
                    r.overtake_timer -= DT
                    if r.overtake_timer <= 0: r.is_overtaking = False; r.overtake_eval_cd = 1.0; r.overtake_target = None; r.overtake_target_lane = None 
                if r.is_evading:
                    r.evade_timer -= DT
                    if r.evade_timer <= 0: r.is_evading = False
                
                if r.section > 10 and r.overtake_eval_cd <= 0 and not r.is_overtaking:
                    if r.is_blocked and closest_blocker:
                        r.is_overtaking = True; r.overtake_timer = 1.5; r.overtake_eval_cd = 2.5
                        r.overtake_target = closest_blocker 
                        target_prob = (20 * math.log10(max(closest_blocker.intel, 1) * 0.1)) / 100.0
                        if random.random() < target_prob: closest_blocker.is_evading = True; closest_blocker.evade_timer = 1.5
                    else:
                        for target in self.runners:
                            if target.id == r.id: continue
                            dist_diff = target.dist - r.dist
                            if 0 < dist_diff <= 20.0:
                                speed_diff = r.speed - target.speed
                                time_to_catch = dist_diff / speed_diff if speed_diff > 0 else 999
                                if (time_to_catch < 15.0 and r.target_speed > target.target_speed) or (target.is_blocked and r.target_speed > target.speed):
                                    prob = (20 * math.log10(max(r.intel, 1) * 0.1)) / 100.0
                                    if random.random() < prob:
                                        r.is_overtaking = True; r.overtake_timer = 1.5; r.overtake_eval_cd = 2.5
                                        r.overtake_target = target 
                                        target_prob = (20 * math.log10(max(target.intel, 1) * 0.1)) / 100.0
                                        if random.random() < target_prob: target.is_evading = True; target.evade_timer = 1.5
                                    else: r.overtake_eval_cd = 2.0
                                    break

                stam_speed_mod = 0.0
                if r.stam_sys_cd > 0: r.stam_sys_cd -= DT
                if 11 <= r.section <= 15 and r.stam_sys_cd <= 0 and not r.stam_sys_active:
                    activated_skill = None
                    spd_b = math.sqrt(500 * r.spd) * 0.002; guts_b = math.pow(450 * r.guts, 0.597) * 0.0001
                    s_targ = (self.base_speed * STYLE_MODS[r.style]["spd"][2] + 0.01 * self.base_speed) * 1.05 + spd_b + guts_b
                    g_drain = 1.0 + 200.0 / math.sqrt(600 * max(r.guts, 1))
                    req_hp_spurt = (((self.track_len - r.dist) - 60) / max(s_targ, 1)) * ((20 * math.pow(s_targ - self.base_speed + 12, 2) / 144.0) * g_drain)
                    
                    if r.hp < 1.05 * req_hp_spurt:
                        if random.random() < (30 * math.pow(max(r.intel, 1) / 1000.0, 0.03)) / 100.0:
                            activated_skill = "preserve"; r.hp = min(r.hp + r.max_hp * 0.03, r.max_hp)
                    
                    if not activated_skill and pacemaker:
                        u_limit = {"선행": 5.0, "선입": 7.0, "추입": 8.0}.get(r.style, 999) * self.race_mod
                        if (pacemaker.dist - r.dist) > u_limit or r.is_nearby:
                            if random.random() < (20 * math.log10(max(r.intel, 1) * 0.1)) / 100.0:
                                activated_skill = "position"; r.hp -= 24 * (1.2 if r.style == "도주" else 1.0)
                    
                    if not activated_skill:
                        lead_triggered = False
                        for b in self.runners:
                            if b.dist < r.dist:
                                d_diff = r.dist - b.dist
                                if r.style == "도주":
                                    if (b.style == "선행" and d_diff < 5.0) or (b.style in ["선입", "추입"] and d_diff < 9.0): lead_triggered = True
                                elif r.style == "선행":
                                    if (b.style == "선입" and d_diff < 6.0) or (b.style == "추입" and d_diff < 7.0): lead_triggered = True
                                elif r.style == "선입":
                                    if b.style == "추입" and d_diff < 4.0: lead_triggered = True
                                if lead_triggered: break
                        if lead_triggered:
                            if random.random() < (20 * math.log10(max(r.intel, 1) * 0.1)) / 100.0:
                                activated_skill = "lead"; r.hp -= 24 * (1.0 if r.style == "도주" else 0.8)

                    if activated_skill: r.stam_sys_active = activated_skill; r.stam_sys_timer = 2.0
                    else: r.stam_sys_cd = 2.0
                
                if r.stam_sys_active:
                    r.stam_sys_timer -= DT
                    if r.stam_sys_active == "preserve": stam_speed_mod = -0.25
                    elif r.stam_sys_active == "position":
                        stam_speed_mod = ((math.pow(max(r.pow, 1) / 1500.0, 0.5) * 2.0 + math.pow(max(r.guts, 1) / 3000.0, 0.2)) * 0.1) * (0.8 if r.style == "도주" else 1.0)
                    elif r.stam_sys_active == "lead":
                        mult_spd = 1.0 if r.style in ["도주", "선행"] else 0.8
                        if r.style == "도주" and not any(o.style == "도주" and o.id != r.id and abs(o.dist - r.dist) <= 10.0 for o in self.runners): mult_spd = 4.0
                        stam_speed_mod = math.pow(max(r.guts, 1) / 2000.0, 0.5) * 0.3 * mult_spd
                    if r.stam_sys_timer <= 0: r.stam_sys_active = None; r.stam_sys_cd = 1.0

                t_mod = STYLE_MODS[r.style]["spd"][r.phase]
                base_target_speed = self.base_speed * t_mod
                spd_bonus = math.sqrt(500 * r.spd) * 0.002
                guts_bonus = math.pow(450 * r.guts, 0.597) * 0.0001
                spurt_target = (self.base_speed * STYLE_MODS[r.style]["spd"][2] + 0.01 * self.base_speed) * 1.05 + spd_bonus + guts_bonus

                if r.hp <= 0: r.is_exhausted = True; r.is_spurting = False
                else:
                    r.is_exhausted = False 
                    if r.phase == 2 and not r.is_spurting:
                        guts_drain_mod = 1.0 + 200.0 / math.sqrt(600 * max(r.guts, 1))
                        req_hp = (((self.track_len - r.dist) - 60) / max(spurt_target, 1)) * ((20 * math.pow(spurt_target - self.base_speed + 12, 2) / 144.0) * guts_drain_mod)
                        if r.hp >= req_hp: r.is_spurting = True

                target_speed = base_target_speed * r.pace_mode_mod
                if r.is_blocked and closest_blocker: target_speed = min(target_speed, closest_blocker.speed + 0.1)
                if r.is_overtaking: target_speed *= 1.05
                if r.is_evading: target_speed *= 1.04
                
                target_speed += stam_speed_mod
                target_speed += r.skill_mod_target_speed 
                
                if r.is_exhausted: target_speed = (0.85 * self.base_speed) * (math.sqrt(200 * max(r.guts, 1)) * 0.001)
                elif r.is_spurting: target_speed = spurt_target
                
            r.target_speed = target_speed

            accel = math.sqrt(500 * r.pow) * 0.002 * STYLE_MODS[r.style]["acc"][r.phase]
            if r.is_spurting and r.pow > 1000: accel += (0.05 + (r.pow - 1000) * 0.00015) * (0.8 if r.has_kakari_history else 1.0)
            if r.is_start_dash:
                if r.speed < self.base_speed * 0.85: accel += 24.0
                else: r.is_start_dash = False
                
            accel += r.skill_mod_accel 

            if r.is_exhausted: decel = 1.2
            elif r.pace_mode == "PaceDown": decel = 0.5
            elif r.phase == 0: decel = 1.2
            elif r.phase == 1: decel = 0.8
            elif r.phase == 2: decel = 1.0
            else: decel = 0.8

            if r.speed < target_speed: r.speed = min(r.speed + accel * DT, target_speed)
            elif r.speed > target_speed: r.speed = max(r.speed - decel * DT, target_speed)

            if not r.is_exhausted:
                hp_drain = 20 * math.pow(r.speed - self.base_speed + 12, 2) / 144.0
                hp_drain *= (1.6 if r.is_kakari else (0.6 if r.pace_mode == "PaceDown" else 1.0))
                if r.phase == 2: hp_drain *= (1.0 + 200.0 / math.sqrt(600 * max(r.guts, 1)))
                r.hp = max(0, r.hp - hp_drain * DT)

            r.dist += r.speed * DT
            
            if r.bump_cd > 0: r.bump_cd -= DT
            
            # 필살기 벌림 효과 처리
            if r.ult_part_timer > 0:
                r.ult_part_timer -= DT
                push_amount = 0.018 * DT * 60
                origin = getattr(r, 'ult_part_origin', r.lane)
                max_disp = getattr(r, '_unique_part_max', 0.3)
                new_lane = r.lane + r.ult_part_dir * push_amount
                if abs(new_lane - origin) <= max_disp:
                    r.lane = max(0.0, min(new_lane, 1.5))
                    r.target_lane = r.lane
            elif getattr(r, '_unique_part_max', None) is not None:
                r._unique_part_max = None
            
            active_contesters = [o for o in contesting_runners if o.bump_cd <= 0]
            dist_to_target_lane = abs(r.lane - r.target_lane)
            is_lane_path_blocked = False
            
            if r.target_lane > r.lane and any(o.lane > r.lane for o in active_contesters): is_lane_path_blocked = True
            if r.target_lane < r.lane and any(o.lane < r.lane for o in active_contesters): is_lane_path_blocked = True

            if r.bump_cd <= 0 and (dist_to_target_lane <= 0.04 or is_lane_path_blocked):
                if r.is_overtaking and r.overtake_target:
                    min_l, max_l = self.get_cluster_bounds(r.overtake_target)
                    c1 = min(1.5, max_l + 0.08) 
                    c2 = max(0.0, min_l - 0.08) 
                    
                    left_blocked = any(o.lane < r.lane for o in active_contesters)
                    right_blocked = any(o.lane > r.lane for o in active_contesters)
                    
                    dist_c1 = abs(r.lane - c1)
                    dist_c2 = abs(r.lane - c2)
                    weight_c1 = 1.0
                    weight_c2 = 0.85 if r.phase == 2 else 1.0
                    
                    if left_blocked and not right_blocked: r.overtake_target_lane = c1
                    elif right_blocked and not left_blocked: r.overtake_target_lane = c2
                    else:
                        if (dist_c2 * weight_c2) <= (dist_c1 * weight_c1): r.overtake_target_lane = c2
                        else: r.overtake_target_lane = c1
                            
                    r.target_lane = r.overtake_target_lane
                else:
                    if r.is_exhausted: r.target_lane = r.lane 
                    elif r.pace_mode == "PaceDown":
                        if not r.pacedown_target_set: r.target_lane = min(1.5, r.lane + 0.25); r.pacedown_target_set = True
                    elif r.section >= 12 and r.lane > 0.4:
                        outer_lane_target = min(1.0, r.lane / 0.1) * 0.5 + random.uniform(0, 0.1)
                        r.target_lane = min(r.lane + 0.08, outer_lane_target)
                    else:
                        inside_blockers = [o for o in self.runners if o.id != r.id and o.bump_cd <= 0 and o.dist > r.dist and abs(o.lane - r.lane) <= (1.75 * 0.08) and o.lane <= r.lane]
                        if r.phase == 1 and inside_blockers:
                            worst_blocker = max(inside_blockers, key=lambda x: x.lane)
                            r.target_lane = min(r.lane + 0.08, worst_blocker.lane + (2 * 0.08))
                        else:
                            if r.dist <= 200: r.target_lane = r.lane - 0.08 
                            else: r.target_lane = r.lane 

            if r.pace_mode != "PaceDown": r.pacedown_target_set = False
            if not r.is_overtaking: r.overtake_target_lane = None

            r.target_lane = max(0.0, min(r.target_lane, 1.5))
            time_to_change_lane = 1.0 
            lane_move_speed = max((abs(r.target_lane - r.lane) / time_to_change_lane) * DT, 0.01 * DT * 60)

            actual_move = 0.0; bump_target = None
            if r.lane < r.target_lane:
                blocking_rights = [o for o in active_contesters if o.lane > r.lane]
                if not blocking_rights: actual_move = min(lane_move_speed, r.target_lane - r.lane)
                else: bump_target = min(blocking_rights, key=lambda x: x.lane - r.lane)
            elif r.lane > r.target_lane:
                blocking_lefts = [o for o in active_contesters if o.lane < r.lane]
                if not blocking_lefts: actual_move = -min(lane_move_speed, r.lane - r.target_lane)
                else: bump_target = min(blocking_lefts, key=lambda x: r.lane - x.lane)
            
            if bump_target and r.bump_cd <= 0:
                pow_ratio = max(r.pow, 1) / max(bump_target.pow, 1)
                if pow_ratio >= 1.1: 
                    actual_move = min(lane_move_speed, r.target_lane - r.lane) if r.lane < r.target_lane else -min(lane_move_speed, r.lane - r.target_lane)
                    push_dir = 0.08 if r.lane < r.target_lane else -0.08
                    bump_target.target_lane = max(0.0, min(bump_target.lane + push_dir, 1.5)) 
                    bump_target.speed = max(bump_target.speed - 0.2, self.base_speed * 0.8) 
                    bump_target.hp -= 2.0; bump_target.bump_cd = 0.8 
                elif pow_ratio <= 0.9: 
                    actual_move = 0.0; bounce_dir = -0.04 if r.lane < r.target_lane else 0.04
                    r.target_lane = max(0.0, min(r.lane + bounce_dir, 1.5)) 
                    r.speed = max(r.speed - 0.2, self.base_speed * 0.8) 
                    r.hp -= 2.0; r.bump_cd = 0.8 
                else:
                    actual_move = 0.0
                    r.target_lane = max(0.0, min(r.lane - 0.04 if r.lane <= bump_target.lane else r.lane + 0.04, 1.5))
                    bump_target.target_lane = max(0.0, min(bump_target.lane + 0.04 if bump_target.lane >= r.lane else bump_target.lane - 0.04, 1.5))
                    r.speed = max(r.speed - 0.05, self.base_speed * 0.8); r.hp -= 0.5
                    bump_target.speed = max(bump_target.speed - 0.05, self.base_speed * 0.8); bump_target.hp -= 0.5
                    r.bump_cd = 0.5; bump_target.bump_cd = 0.5

            r.lane = max(0.0, min(r.lane + actual_move, 1.5))

            if r.is_start_dash: r.active_states.append("Dash")
            if r.is_spurting: r.active_states.append("Spurt")
            if r.is_exhausted: r.active_states.append("Exhaust")
            if r.is_kakari: r.active_states.append("Kakari")
            if r.pace_mode != "Normal": r.active_states.append(r.pace_mode)
            if r.is_overtaking: r.active_states.append("Overtake")
            if r.stam_sys_active: r.active_states.append("StaminaSkill")

            current_frame["r"].append([r.id, round(r.dist, 2), round(r.lane, 2), round(r.speed, 2), int(r.hp), r.active_states])
        if current_frame["r"]: self.frames.append(current_frame)

@app.route('/api/create_room_final', methods=['POST'])
def create_room_final():
    cleanup_old_rooms(); data = request.json
    if not data.get('url') or not data.get('participants') or not data.get('scheduled_time'): return jsonify({"success": False, "message": "데이터 누락"}), 400
    room_id = generate_room_id(); track_len = 2000; runners = []
    
    STAT_WEIGHTS = {
        "도주": {'spd': 0.25, 'stam': 0.19, 'pow': 0.18, 'guts': 0.15, 'intel': 0.23},
        "선행": {'spd': 0.23, 'stam': 0.22, 'pow': 0.21, 'guts': 0.13, 'intel': 0.21},
        "선입": {'spd': 0.23, 'stam': 0.21, 'pow': 0.24, 'guts': 0.13, 'intel': 0.19},
        "추입": {'spd': 0.24, 'stam': 0.20, 'pow': 0.25, 'guts': 0.13, 'intel': 0.18}
    }
    
    skinList = ['ti.png', 'nin.png', 'vic.png', 'cho.png', 'shal.png', 'skin01.png', 'skin02.png', 'skin03.png', 'skin04.png', 'skin05.png', 'skin06.png', 'skin07.png', 'skin08.png', 'skin21.png', 'skin22.png', 'skin23.png', 'skin24.png', 'skin25.png', 'skin31.png', 'skin32.png', 'skin33.png', 'skin34.png', 'skin35.png', 'skin36.png', 'skin41.png', 'skin42.png', 'skin43.png', 'skin51.png', 'skin52.png', 'skin53.png', 'skin54.png', 'skin55.png', 'skin61.png', 'skin62.png', 'skin63.png', 'skin64.png']
    
    for idx, p_name in enumerate(data['participants']):
        style = random.choice(["도주", "선행", "선입", "추입"])
        total_points = random.randint(4000, 4500) 
        raw_stats = {k: int(total_points * w) for k, w in STAT_WEIGHTS[style].items()}
        
        safety_break = 0
        while any(v > 1200 for v in raw_stats.values()) and safety_break < 100:
            excess = 0
            for k in raw_stats:
                if raw_stats[k] > 1200:
                    excess += raw_stats[k] - 1200; raw_stats[k] = 1200
            while excess > 0:
                available_keys = [k for k in raw_stats if raw_stats[k] < 1200]
                if not available_keys: 
                    excess = 0; break 
                target = random.choice(available_keys); raw_stats[target] += 1; excess -= 1
            safety_break += 1
        
        row = idx // 18; col = idx % 18
        start_lane = col * 0.08; start_dist = -row * 2.5 
        runner = Uma(idx, p_name, style, raw_stats, random.choice([1.05, 1.02, 1.00, 0.98, 0.95]), track_len, start_lane, start_dist)
        
        # 50% 확률로 기본 스킨 + 색상 변경, 나머지는 커스텀 스킨 (색상 원본)
        if random.random() < 0.3:
            runner.skin = "runner.png"
            runner.hue = random.randint(0, 359)
        else:
            runner.skin = random.choice(skinList)
            runner.hue = 0
            
        runners.append(runner)
        
    ROOMS_DB[room_id] = {
        "url": data['url'], 
        "participants": [{"id": r.id, "name": r.name, "style": r.style, "skin": r.skin, "hue": r.hue} for r in runners],
        "replay_data": RaceSimulator(runners, track_len).run(), "created_at": time.time(),
        "scheduled_time": data['scheduled_time'], "bgm": data.get('bgm', 'none'), "allow_custom_chat": data.get('allow_custom_chat', False)
    }
    return jsonify({"success": True, "room_id": room_id})

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    cleanup_old_rooms()
    if room_id.upper() in ROOMS_DB:
        return jsonify({"success": True, "data": ROOMS_DB[room_id.upper()]})
    return jsonify({"success": False, "message": "방 없음"}), 404

if __name__ == '__main__': app.run(host='0.0.0.0', port=5000, debug=True)