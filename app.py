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

# ==========================================
# 🐎 [백엔드 코어] 물리 엔진 및 AI 로직
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
    def __init__(self, uid, name, style, raw_stats, cond_mult, track_len, start_lane, start_dist):
        self.id = uid; self.name = name; self.original_style = style; self.style = style 
        self.spd = raw_stats['spd'] * cond_mult; self.stam = raw_stats['stam'] * cond_mult
        self.pow = raw_stats['pow'] * cond_mult; self.guts = raw_stats['guts'] * cond_mult; self.intel = raw_stats['intel'] * cond_mult
        
        self.track_len = track_len
        self.max_hp = 0.8 * STYLE_MODS[style]["hp"] * self.stam + track_len; self.hp = self.max_hp
        self.dist = start_dist; self.speed = 3.0; self.lane = start_lane; self.target_speed = 3.0
        self.target_lane = start_lane
        
        self.start_delay = math.floor(random.uniform(0, 0.1) / DT) * DT 
        self.section = 1; self.phase = 0
        self.is_start_dash = True; self.is_exhausted = False; self.is_spurting = False
        
        intel_bonus = max(self.intel, 1) * 0.1
        kakari_prob = math.pow(6.5 / math.log10(intel_bonus + 1), 2) / 100.0
        self.is_kakari_planned = random.random() < kakari_prob
        self.kakari_target_section = random.randint(2, 9) if self.is_kakari_planned else 0
        self.is_kakari = False; self.kakari_timer = 0; self.has_kakari_history = False
        
        self.pace_mode = "Normal"; self.pace_mode_mod = 1.0
        self.pace_eval_cd = 0.0
        self.pacedown_target_set = False 
        
        self.is_overtaking = False; self.overtake_timer = 0; self.overtake_target = None
        self.overtake_target_lane = None 
        self.is_evading = False; self.evade_timer = 0
        self.overtake_eval_cd = 0.0
        
        self.is_blocked = False; self.is_contested = False; self.is_followed = False; self.is_nearby = False
        
        self.stam_sys_cd = 0.0; self.stam_sys_active = None; self.stam_sys_timer = 0.0
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
        
    def get_cluster_bounds(self, center_runner):
        visited = set([center_runner.id])
        queue = [center_runner]
        min_lane = center_runner.lane
        max_lane = center_runner.lane
        
        while queue:
            curr = queue.pop(0)
            for o in self.runners:
                if o.id in visited: continue
                if abs(o.dist - curr.dist) <= 3.0 and abs(o.lane - curr.lane) <= (2 * 0.08):
                    visited.add(o.id)
                    queue.append(o)
                    if o.lane < min_lane: min_lane = o.lane
                    if o.lane > max_lane: max_lane = o.lane
                    
        return min_lane, max_lane

    def tick(self):
        current_frame = {"t": round(self.time, 2), "r": []}
        leader = max(self.runners, key=lambda x: x.dist)

        for r in self.runners:
            if r.dist >= self.track_len: continue
            r.active_states = []
            
            if self.time < r.start_delay:
                current_frame["r"].append([r.id, round(r.dist, 2), round(r.lane, 2), 0.0, int(r.hp), ["LateStart"]])
                continue

            r.section = max(1, math.floor(r.dist / self.section_len) + 1)
            if r.section <= 4: r.phase = 0
            elif r.section <= 16: r.phase = 1
            else: r.phase = 2

            r.is_blocked = False; r.is_contested = False; r.is_followed = False; r.is_nearby = False
            closest_blocker = None; min_block_dist = 999.0
            contesting_runners = []

            for o in self.runners:
                if o.id == r.id: continue
                dist_diff = o.dist - r.dist
                lane_diff = abs(o.lane - r.lane)

                if 0 < dist_diff <= 2.0 and lane_diff <= (0.5 * 0.08):
                    r.is_blocked = True
                    if dist_diff < min_block_dist:
                        min_block_dist = dist_diff; closest_blocker = o
                if abs(dist_diff) <= 1.0 and lane_diff <= (2.0 * 0.08):
                    r.is_contested = True; contesting_runners.append(o)
                if 0 < -dist_diff <= 2.5 and lane_diff <= (1.0 * 0.08): r.is_followed = True
                if abs(dist_diff) <= 3.0 and lane_diff <= (3.0 * 0.08): r.is_nearby = True

            if r.section == r.kakari_target_section and not r.has_kakari_history:
                r.is_kakari = True; r.has_kakari_history = True; r.kakari_timer = 12.0
            if r.is_kakari:
                r.kakari_timer -= DT
                if r.kakari_timer % 3.0 < DT and random.random() < 0.55: r.kakari_timer = 0
                if r.kakari_timer <= 0: r.is_kakari = False; r.style = r.original_style

            if r.pace_eval_cd > 0: 
                r.pace_eval_cd -= DT
                if r.pace_eval_cd <= 0:
                    r.pace_mode = "Normal"; r.pace_mode_mod = 1.0
            
            if r.section > 10 and r.pace_mode in ["PaceDown", "PaceUp", "PaceUpEx", "Chase"]:
                r.pace_mode = "Normal"; r.pace_mode_mod = 1.0; r.pace_eval_cd = 0.0

            if r.section <= 10 and r.pace_eval_cd <= 0 and r.pace_mode == "Normal":
                is_escape = (r.style == "도주" or (leader.style != "도주" and r.id == leader.id))
                activated = False
                if is_escape:
                    behind_strats = {"도주": ["선행", "선입", "추입"], "선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                    if any(o.dist > r.dist and o.style in behind_strats[r.style] for o in self.runners):
                        r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0; activated = True
                    elif r.id == leader.id:
                        diff_2nd = leader.dist - max([o.dist for o in self.runners if o.id != leader.id] + [0])
                        limit = 12.5 if sum(1 for o in self.runners if o.style == "도주") == 1 else 4.5
                        if diff_2nd <= limit:
                            if random.random() < (1.0 if r.is_kakari else (20 * math.log10(max(r.intel, 1) * 0.1))/100.0):
                                r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04; activated = True
                            else: r.pace_eval_cd = 2.0
                    elif r.is_kakari: r.pace_mode = "Chase"; r.pace_mode_mod = 1.05; activated = True
                else:
                    behind_strats = {"선행": ["선입", "추입"], "선입": ["추입"], "추입": []}
                    if leader.style in behind_strats.get(r.style, []):
                        r.pace_mode = "PaceUpEx"; r.pace_mode_mod = 2.0; activated = True
                    else:
                        dist_diff = leader.dist - r.dist
                        upper = {"선행": 5.0, "선입": 7.0, "추입": 8.0}.get(r.style, 999) * self.race_mod
                        lower = {"선행": 3.0, "선입": 6.5, "추입": 7.5}.get(r.style, 0) * (self.race_mod if r.style != "선행" else 1.0)
                        if dist_diff < lower:
                            r.pace_mode = "PaceDown"; r.pace_mode_mod = 0.915 if r.phase == 0 else 0.945; activated = True
                        elif dist_diff >= upper:
                            if random.random() < (1.0 if r.is_kakari else (15 * math.log10(max(r.intel, 1) * 0.1))/100.0):
                                r.pace_mode = "PaceUp"; r.pace_mode_mod = 1.04; activated = True
                            else: r.pace_eval_cd = 2.0
                if activated: r.pace_eval_cd = 2.0

            if r.overtake_eval_cd > 0: r.overtake_eval_cd -= DT
            if r.is_overtaking:
                r.overtake_timer -= DT
                if r.overtake_timer <= 0:
                    r.is_overtaking = False; r.overtake_eval_cd = 1.0; r.overtake_target = None
                    r.overtake_target_lane = None 
            if r.is_evading:
                r.evade_timer -= DT
                if r.evade_timer <= 0: r.is_evading = False
            
            if r.section > 10 and r.overtake_eval_cd <= 0 and not r.is_overtaking:
                if r.is_blocked and closest_blocker:
                    # 💡 추월 지속시간 1.5초로 수정
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

            # 스태미나 스킬 시스템
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
                
                if not activated_skill:
                    u_limit = {"선행": 5.0, "선입": 7.0, "추입": 8.0}.get(r.style, 999) * self.race_mod
                    if (leader.dist - r.dist) > u_limit or r.is_nearby:
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

            # 목표 속도 산출
            t_mod = STYLE_MODS[r.style]["spd"][r.phase]
            base_target_speed = self.base_speed * t_mod
            spd_bonus = math.sqrt(500 * r.spd) * 0.002
            guts_bonus = math.pow(450 * r.guts, 0.597) * 0.0001
            spurt_target = (self.base_speed * STYLE_MODS[r.style]["spd"][2] + 0.01 * self.base_speed) * 1.05 + spd_bonus + guts_bonus

            if r.hp <= 0:
                r.is_exhausted = True; r.is_spurting = False
            else:
                r.is_exhausted = False 
                if r.phase == 2 and not r.is_spurting:
                    guts_drain_mod = 1.0 + 200.0 / math.sqrt(600 * max(r.guts, 1))
                    req_hp = (((self.track_len - r.dist) - 60) / max(spurt_target, 1)) * ((20 * math.pow(spurt_target - self.base_speed + 12, 2) / 144.0) * guts_drain_mod)
                    if r.hp >= req_hp: r.is_spurting = True

            target_speed = base_target_speed * r.pace_mode_mod
            
            if r.is_blocked and closest_blocker:
                target_speed = min(target_speed, closest_blocker.speed + 0.1)

            if r.is_overtaking: target_speed *= 1.05
            if r.is_evading: target_speed *= 1.04
            target_speed += stam_speed_mod
            
            if r.is_exhausted: 
                target_speed = (0.85 * self.base_speed) * (math.sqrt(200 * max(r.guts, 1)) * 0.001)
            elif r.is_spurting: 
                target_speed = spurt_target

            r.target_speed = target_speed

            accel = math.sqrt(500 * r.pow) * 0.002 * STYLE_MODS[r.style]["acc"][r.phase]
            if r.is_spurting and r.pow > 1000: accel += (0.05 + (r.pow - 1000) * 0.00015) * (0.8 if r.has_kakari_history else 1.0)
            if r.is_start_dash:
                if r.speed < self.base_speed * 0.85: accel += 24.0
                else: r.is_start_dash = False

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
            
            # ==========================================
            # 💡 [지능형 레인 탐색(Pathfinding) AI 엔진] 
            # ==========================================
            dist_to_target_lane = abs(r.lane - r.target_lane)
            is_lane_path_blocked = False
            
            if r.target_lane > r.lane and any(o.lane > r.lane for o in contesting_runners): is_lane_path_blocked = True
            if r.target_lane < r.lane and any(o.lane < r.lane for o in contesting_runners): is_lane_path_blocked = True

            if dist_to_target_lane <= 0.04 or is_lane_path_blocked:
                
                if r.is_overtaking and r.overtake_target:
                    if r.overtake_target_lane is None:
                        min_l, max_l = self.get_cluster_bounds(r.overtake_target)
                        c1 = max(0.0, min_l - 0.08)
                        c2 = min(1.5, max_l + 0.08)
                        
                        left_blocked = any(o.lane < r.lane for o in contesting_runners)
                        right_blocked = any(o.lane > r.lane for o in contesting_runners)
                        
                        if abs(r.lane - c1) < abs(r.lane - c2):
                            r.overtake_target_lane = c2 if left_blocked and not right_blocked else c1
                        else:
                            r.overtake_target_lane = c1 if right_blocked and not left_blocked else c2
                            
                    r.target_lane = r.overtake_target_lane
                else:
                    if r.is_exhausted:
                        r.target_lane = r.lane 
                    elif r.pace_mode == "PaceDown":
                        if not r.pacedown_target_set:
                            r.target_lane = min(1.5, r.lane + 0.25)
                            r.pacedown_target_set = True
                    elif r.section >= 12 and r.lane > 0.4:
                        outer_lane_target = min(1.0, r.lane / 0.1) * 0.5 + random.uniform(0, 0.1)
                        # 💡 오타 수정 (0.05 -> 0.08)
                        r.target_lane = min(r.lane + 0.08, outer_lane_target)
                    else:
                        inside_blockers = [o for o in self.runners if o.id != r.id and o.dist > r.dist and abs(o.lane - r.lane) <= (1.75 * 0.08) and o.lane <= r.lane]
                        if r.phase == 1 and inside_blockers:
                            worst_blocker = max(inside_blockers, key=lambda x: x.lane)
                            r.target_lane = min(r.lane + 0.08, worst_blocker.lane + (2 * 0.08))
                        else:
                            if r.dist <= 200:
                                r.target_lane = r.lane - 0.08 
                            else:
                                r.target_lane = r.lane 

            if r.pace_mode != "PaceDown": r.pacedown_target_set = False
            if not r.is_overtaking: r.overtake_target_lane = None

            r.target_lane = max(0.0, min(r.target_lane, 1.5))
            
            # 💡 [부드러운 사선 방향 차선 변경(Steering) 로직 적용]
            # 1초에 걸쳐 차선을 변경하도록 보간 속도 설정 (기존에는 너무 휙휙 꺾임)
            time_to_change_lane = 1.0 # 1초에 걸쳐 이동
            lane_move_speed = (abs(r.target_lane - r.lane) / time_to_change_lane) * DT
            
            # 최소 이동 속도 보장 (너무 느리게 이동하여 목표를 못 잡는 현상 방지)
            min_steer_speed = 0.01 * DT * 60 
            lane_move_speed = max(lane_move_speed, min_steer_speed)

            actual_move = 0.0
            if r.lane < r.target_lane:
                if not any(o.lane > r.lane for o in contesting_runners):
                    actual_move = min(lane_move_speed, r.target_lane - r.lane)
            elif r.lane > r.target_lane:
                if not any(o.lane < r.lane for o in contesting_runners):
                    actual_move = -min(lane_move_speed, r.lane - r.target_lane)
            
            r.lane += actual_move

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
    if not data.get('url') or not data.get('participants') or not data.get('scheduled_time'):
        return jsonify({"success": False, "message": "데이터 누락"}), 400
        
    room_id = generate_room_id(); track_len = 2000; runners = []
    
    STAT_WEIGHTS = {
        "도주": {'spd': 0.25, 'stam': 0.19, 'pow': 0.18, 'guts': 0.15, 'intel': 0.23},
        "선행": {'spd': 0.23, 'stam': 0.22, 'pow': 0.21, 'guts': 0.13, 'intel': 0.21},
        "선입": {'spd': 0.23, 'stam': 0.21, 'pow': 0.24, 'guts': 0.13, 'intel': 0.19},
        "추입": {'spd': 0.24, 'stam': 0.20, 'pow': 0.25, 'guts': 0.13, 'intel': 0.18}
    }
    
    for idx, p_name in enumerate(data['participants']):
        style = random.choice(["도주", "선행", "선입", "추입"])
        total_points = random.randint(4000, 4500) 
        raw_stats = {k: int(total_points * w) for k, w in STAT_WEIGHTS[style].items()}
        
        while any(v > 1200 for v in raw_stats.values()):
            excess = 0
            for k in raw_stats:
                if raw_stats[k] > 1200:
                    excess += raw_stats[k] - 1200
                    raw_stats[k] = 1200
            while excess > 0:
                available_keys = [k for k in raw_stats if raw_stats[k] < 1200]
                if not available_keys: break 
                target = random.choice(available_keys)
                raw_stats[target] += 1
                excess -= 1
        
        row = idx // 18
        col = idx % 18
        start_lane = col * 0.08 
        start_dist = -row * 2.5 
        
        runner = Uma(idx, p_name, style, raw_stats, random.choice([1.05, 1.02, 1.00, 0.98, 0.95]), track_len, start_lane, start_dist)
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