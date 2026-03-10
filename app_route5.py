# -*- coding: utf-8 -*-
import itertools
import time
import base64
import streamlit.components.v1 as components
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import requests
import streamlit as st

# =========================
# Config
# =========================
UBS_PELOTAS = {
    "UBS Sansca — Centro": (-31.7569244, -52.3467475),
    "UBS Porto — Porto": (-31.7789381, -52.3363786),
    "UBS Fátima — São Gonçalo": (-31.7733215, -52.3268329),
    "UBS Simões Lopes — Simões Lopes": (-31.7738246, -52.3565292),
    "UBS Osório — Porto": (-31.7804775, -52.3500625),
    "UBS PAM Fragata — Fragata": (-31.7545839, -52.3836795),
    "UBS Cohab Guabiroba — Fragata": (-31.7477222, -52.3676325),
    "UBS Fraget — Fragata": (-31.7524950, -52.3688850),
    "UBS Areal I — Areal": (-31.7547494, -52.3286805),
    "UBS CSU Areal — Areal": (-31.7553824, -52.3170841),
    "UBS CSU Cruzeiro — Areal": (-31.7636424, -52.3231341),
    "UBS Leocádia — Areal": (-31.7509573, -52.3234772),
    "UBS Bom Jesus — Areal": (-31.7460013, -52.3195169),
    "UBS Santa Terezinha — Três Vendas": (-31.7258671, -52.3495507),
    "UBS Salgado Filho — Três Vendas": (-31.7328613, -52.3366328),
    "UBS Vila Municipal — Três Vendas": (-31.7304988, -52.3268676),
    "UBS Arco Íris — Três Vendas": (-31.7201459, -52.3052027),
    "UBS Cohab Lindóia — Três Vendas": (-31.7097515, -52.3479600),
}

MAX_STOPS = 8
OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/{profile}/{coords}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass
class Stop:
    label: str
    address: str
    lat: float
    lon: float


# =========================
# Geocoding
# =========================
@st.cache_data(show_spinner=False, ttl=24 * 3600)
def geocode_pelotas(address: str) -> Optional[Tuple[float, float]]:
    # Tenta variações do endereço para aumentar chance de geocoding
    queries = [
        f"{address}, Pelotas, RS, Brasil",
        f"{address}, Pelotas, Brasil",
        f"{address}, Pelotas",
    ]
    headers = {"User-Agent": "rota-visita-ufpel/1.0 (educational)"}
    for q in queries:
        try:
            params = {"q": q, "format": "json", "limit": 1, "countrycodes": "br"}
            r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=(6, 25))
            r.raise_for_status()
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception:
            continue
    return None


# =========================
# OSRM table
# =========================
@st.cache_data(show_spinner=False, ttl=2 * 3600)
def osrm_table(profile: str, points: List[Tuple[float, float]]) -> Tuple[List[List[float]], List[List[float]]]:
    coords = ";".join([f"{lon},{lat}" for (lat, lon) in points])
    url = OSRM_TABLE_URL.format(profile=profile, coords=coords)
    params = {"annotations": "duration,distance"}
    r = requests.get(url, params=params, timeout=(4, 25))
    r.raise_for_status()
    js = r.json()
    if "durations" not in js or "distances" not in js:
        raise RuntimeError("OSRM não retornou matriz de custo.")
    return js["durations"], js["distances"]


def best_cycle_order(cost_matrix: List[List[float]]) -> Tuple[List[int], float]:
    n = len(cost_matrix) - 1
    if n <= 0:
        return [0, 0], 0.0
    best_cost = float("inf")
    best_perm = None
    for perm in itertools.permutations(range(1, n + 1), n):
        total = 0.0
        prev = 0
        ok = True
        for idx in perm:
            c = cost_matrix[prev][idx]
            if c is None:
                ok = False
                break
            total += float(c)
            prev = idx
        c_back = cost_matrix[prev][0]
        if c_back is None:
            ok = False
        else:
            total += float(c_back)
        if ok and total < best_cost:
            best_cost = total
            best_perm = perm
    if best_perm is None:
        return [0, 0], float("inf")
    return [0, *best_perm, 0], best_cost


@st.cache_data(show_spinner=False, ttl=10 * 60)
def search_suggestions(query: str) -> List[Tuple[str, float, float]]:
    """Busca até 5 sugestões de endereço em Pelotas via Nominatim."""
    if len(query.strip()) < 4:
        return []
    params = {
        "q": f"{query}, Pelotas, RS, Brasil",
        "format": "json",
        "limit": 5,
        "addressdetails": 1,
        "countrycodes": "br",
    }
    headers = {"User-Agent": "rota-visita-ufpel/1.0 (educational)"}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=(6, 15))
        r.raise_for_status()
        data = r.json()
        results = []
        for item in data:
            display = item.get("display_name", "")
            # Simplifica o label removendo ", Brasil" e partes longas
            parts = display.split(",")
            short = ", ".join(p.strip() for p in parts[:4])
            results.append((short, float(item["lat"]), float(item["lon"])))
        return results
    except Exception:
        return []


def google_maps_link(start_lat, start_lon, waypoints, mode):
    origin = quote_plus(f"{start_lat},{start_lon}")
    destination = origin
    wp = "|".join([f"{lat},{lon}" for (lat, lon) in waypoints])
    wp_q = quote_plus(wp) if wp else ""
    base = "https://www.google.com/maps/dir/?api=1"
    if wp_q:
        return f"{base}&origin={origin}&destination={destination}&travelmode={mode}&waypoints={wp_q}"
    return f"{base}&origin={origin}&destination={destination}&travelmode={mode}"


def fmt_time(seconds):
    if seconds == float("inf"):
        return "—"
    m = int(round(seconds / 60))
    h, mm = m // 60, m % 60
    return f"{h}h {mm}min" if h > 0 else f"{mm}min"


def fmt_dist(meters):
    if meters == float("inf"):
        return "—"
    return f"{meters / 1000.0:.2f} km"





# =========================
# Page setup
# =========================
st.set_page_config(page_title="Rota de Visitas — Pelotas", layout="centered")

st.markdown(
    """
    <style>
      header, [data-testid="stToolbar"], [data-testid="stHeader"],
      [data-testid="stDecoration"], #MainMenu, footer { display:none !important; visibility:hidden !important; }

      .block-container { padding-top: 0.3rem !important; padding-bottom: 1.5rem !important; }

      h2 { font-size: 1.55rem !important; margin: 0.2rem 0 !important; }

      /* stop card */
      .stop-card {
        background: #f8f9fb;
        border: 1px solid #e2e6ea;
        border-radius: 14px;
        padding: 12px 14px 8px 14px;
        margin-bottom: 10px;
      }
      .stop-label {
        font-size: 1rem;
        font-weight: 700;
        color: #444;
        margin-bottom: 6px;
      }

      /* big calc button */
      div[data-testid="stButton"] > button[kind="primary"] {
        width: 100%;
        padding: 0.75rem 1rem;
        font-size: 1.15rem !important;
        font-weight: 700 !important;
        border-radius: 12px !important;
      }

      /* add/remove buttons */
      .small-btn button {
        font-size: 0.9rem !important;
        padding: 0.3rem 0.8rem !important;
        border-radius: 8px !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Banner
banner_path = Path(__file__).parent / "assets" / "banner.pdf.a4.png"
if banner_path.exists():
    st.image(str(banner_path), use_container_width=True)

# Logos + links (ACIMA do título)
insta_path   = Path(__file__).parent / "assets" / "instagram.png"
enf_path     = Path(__file__).parent / "assets" / "logo.enfermagem.png"
sansca_path  = Path(__file__).parent / "assets" / "sansca.png"

try:
    insta_b64 = base64.b64encode(insta_path.read_bytes()).decode()
    enf_b64   = base64.b64encode(enf_path.read_bytes()).decode()
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:10px;margin:2px 0 10px 0;flex-wrap:wrap;">
            <img src="data:image/png;base64,{insta_b64}" width="22">
            <a href="https://www.instagram.com/amorapele_ufpel/" target="_blank" style="text-decoration:none;font-weight:500;">Amor à Pele</a>
            <span style="color:#ccc">|</span>
            <a href="https://www.instagram.com/g10petsaude/" target="_blank" style="text-decoration:none;font-weight:500;">PET G10</a>
            <span style="color:#ccc">|</span>
            <img src="data:image/png;base64,{enf_b64}" width="22">
            <a href="https://wp.ufpel.edu.br/fen/" target="_blank" style="text-decoration:none;font-weight:500;">Enfermagem — UFPel</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    pass

# Título com foto sansca ao lado (ABAIXO dos links)
try:
    sansca_b64 = base64.b64encode(sansca_path.read_bytes()).decode()
    st.markdown(
        f"""
        <div style="display:flex;align-items:center;gap:14px;margin:4px 0 14px 0;">
            <img src="data:image/png;base64,{sansca_b64}"
                 style="width:120px;height:120px;object-fit:cover;border-radius:10px;flex-shrink:0;
                        box-shadow:0 2px 8px rgba(0,0,0,.15);">
            <div>
                <div style="font-size:1.45rem;font-weight:800;color:#1a1a2e;line-height:1.2;">
                    🗺️ Rotas VDs
                </div>
                <div style="font-size:1rem;font-weight:600;color:#555;margin-top:2px;">
                    sistema GS
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    st.markdown(
        "<h2 style='margin:4px 0 12px 0;'>🗺️ Rotas VDs — sistema GS</h2>",
        unsafe_allow_html=True,
    )

with st.expander("ℹ️ Como funciona", expanded=False):
    st.markdown(
        f"- Adicione até **{MAX_STOPS} paradas** com o botão **＋ Adicionar parada**.\n"
        "- No celular: toque no campo de texto e use o 🎤 **microfone do teclado** para falar.\n"
        "- No iPhone use o **microfone do teclado** diretamente no campo de texto.\n"
        "- A rota é otimizada por **tempo** (OSRM), saindo e voltando à UBS.\n"
        "- Ao final, abra no **Google Maps** ou copie o texto para o WhatsApp."
    )

# =========================
# UBS + Modo
# =========================
colA, colB = st.columns([3, 2])
with colA:
    ubs_name = st.selectbox("🏥 UBS (partida e retorno)", list(UBS_PELOTAS.keys()))
with colB:
    mode = st.radio(
        "Modo de viagem",
        ["driving", "walking"],
        format_func=lambda x: "🚗 Carro" if x == "driving" else "🚶 A pé",
        horizontal=True,
    )

start_lat, start_lon = UBS_PELOTAS[ubs_name]

# =========================
# Session state — lista dinâmica
# =========================
if "num_stops" not in st.session_state:
    st.session_state.num_stops = 1

# Garante que os campos existam
for i in range(1, MAX_STOPS + 1):
    st.session_state.setdefault(f"addr_{i}", "")

st.divider()
st.markdown("### 📍 Paradas")

# Renderiza cada parada
for i in range(1, st.session_state.num_stops + 1):
    st.markdown(
        f"<div class='stop-card'><div class='stop-label'>Parada {i}</div>",
        unsafe_allow_html=True,
    )

    # Campo de texto para digitação
    typed = st.text_input(
        f"Endereço {i}",
        key=f"addr_{i}",
        placeholder="Ex.: Rua General Osório, 100 — Centro",
        label_visibility="collapsed",
    )
    st.caption("📱 No celular: toque no campo e use o ícone 🎤 do teclado para falar o endereço.")

    # Autocomplete: mostra sugestões se o usuário digitou algo
    if typed and len(typed.strip()) >= 4:
        sugestoes = search_suggestions(typed.strip())
        if sugestoes:
            opcoes = ["— selecione uma sugestão —"] + [s[0] for s in sugestoes]
            escolha = st.selectbox(
                f"Sugestões para parada {i}",
                opcoes,
                key=f"sug_{i}",
                label_visibility="collapsed",
            )
            if escolha != "— selecione uma sugestão —":
                # Preenche o campo com a sugestão escolhida
                st.session_state[f"addr_{i}"] = escolha
                # Salva coordenadas já resolvidas para evitar geocoding redundante
                idx_sug = opcoes.index(escolha) - 1
                lat_s, lon_s = sugestoes[idx_sug][1], sugestoes[idx_sug][2]
                st.session_state[f"coords_{i}"] = (lat_s, lon_s)
                st.rerun()

    # Botão remover (exceto se for a única parada)
    if st.session_state.num_stops > 1:
        with st.container():
            if st.button(f"🗑️ Remover parada {i}", key=f"remove_{i}"):
                for j in range(i, st.session_state.num_stops):
                    st.session_state[f"addr_{j}"] = st.session_state.get(f"addr_{j+1}", "")
                    st.session_state[f"coords_{j}"] = st.session_state.get(f"coords_{j+1}")
                st.session_state[f"addr_{st.session_state.num_stops}"] = ""
                st.session_state.pop(f"coords_{st.session_state.num_stops}", None)
                st.session_state.num_stops -= 1
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

# Botão adicionar parada
if st.session_state.num_stops < MAX_STOPS:
    if st.button(f"➕ Adicionar parada  ({st.session_state.num_stops}/{MAX_STOPS})"):
        st.session_state.num_stops += 1
        st.rerun()
else:
    st.caption(f"Limite de {MAX_STOPS} paradas atingido.")

st.divider()

# =========================
# Calcular rota
# =========================
calc = st.button("🚀 Calcular melhor rota", type="primary")

if calc:
    # Coleta endereços preenchidos
    raw = []
    for i in range(1, st.session_state.num_stops + 1):
        addr = (st.session_state.get(f"addr_{i}", "") or "").strip()
        if addr:
            raw.append((i, addr))
        else:
            st.warning(f"Parada {i} está vazia e será ignorada.")

    if not raw:
        st.error("Adicione pelo menos 1 endereço antes de calcular.")
    else:
        with st.spinner("Localizando endereços…"):
            stops: List[Stop] = []
            erros: List[str] = []
            for i, addr in raw:
                # Usa coordenadas já resolvidas pelo autocomplete se disponíveis
                coords = st.session_state.get(f"coords_{i}")
                if coords:
                    lat, lon = coords
                else:
                    loc = geocode_pelotas(addr)
                    if loc is None:
                        erros.append(f"Parada {i}: '{addr}'")
                        continue
                    lat, lon = loc
                    time.sleep(0.2)
                stops.append(Stop(label=f"Parada {i}", address=addr, lat=lat, lon=lon))

        if erros:
            st.warning(
                "⚠️ Não foi possível localizar automaticamente os seguintes endereços:\n\n"
                + "\n".join(f"• {e}" for e in erros)
                + "\n\nDica: use o autocomplete — digite o endereço e selecione uma sugestão da lista."
            )
        if not stops:
            st.error("Nenhum endereço válido encontrado.")
        else:

            points = [(start_lat, start_lon)] + [(s.lat, s.lon) for s in stops]

            with st.spinner("Otimizando rota…"):
                try:
                    durations, distances = osrm_table(mode, points)
                except Exception as e:
                    st.error(f"Erro ao consultar OSRM: {e}")
                    durations, distances = None, None

            if durations is None:
                pass  # erro já exibido acima
            else:
                order, best_sec = best_cycle_order(durations)
                total_m = sum(float(distances[a][b]) for a, b in zip(order[:-1], order[1:]))

                visit_indices = [idx for idx in order if idx != 0]
                ordered_stops = [stops[idx - 1] for idx in visit_indices]

                st.success("✅ Rota otimizada!")

                # Sequência
                seq_lines = [f"**🏥 {ubs_name}**"]
                for s in ordered_stops:
                    seq_lines.append(f"→ **{s.label}**: {s.address}")
                seq_lines.append(f"→ **🏥 {ubs_name}**")
                st.markdown("\n\n".join(seq_lines))

                c1, c2 = st.columns(2)
                c1.metric("⏱️ Tempo estimado", fmt_time(best_sec))
                c2.metric("📏 Distância estimada", fmt_dist(total_m))

                wp_coords = [(s.lat, s.lon) for s in ordered_stops]
                gmaps = google_maps_link(start_lat, start_lon, wp_coords, mode)
                st.link_button("📍 Abrir no Google Maps", gmaps)
                st.caption("Se o Maps reordenar, desative 'otimizar paradas' no app.")

                # Texto para WhatsApp
                modo_str = "🚗 Carro" if mode == "driving" else "🚶 A pé"
                txt_lines = [
                    "🗺️ ROTA DE VISITAS — UFPel / Amor à Pele",
                    f"UBS: {ubs_name}",
                    f"Modo: {modo_str}",
                    f"Tempo estimado: {fmt_time(best_sec)}",
                    f"Distância estimada: {fmt_dist(total_m)}",
                    "",
                    "📍 Sequência:",
                    f"  🏥 {ubs_name} (saída)",
                ]
                for idx, s in enumerate(ordered_stops, start=1):
                    txt_lines.append(f"  {idx}. {s.label}: {s.address}")
                txt_lines.append(f"  🏥 {ubs_name} (retorno)")
                txt_lines.append("")
                txt_lines.append(f"🔗 Google Maps: {gmaps}")
                rota_txt = "\n".join(txt_lines)

                st.divider()
                st.markdown("#### 📋 Copiar para WhatsApp / e-mail")
                st.text_area(
                    label="Selecione tudo e copie:",
                    value=rota_txt,
                    height=230,
                    key="rota_txt_output",
                )

                rota_esc = rota_txt.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
                components.html(
                    f"""
                    <button onclick="
                      navigator.clipboard.writeText(`{rota_esc}`)
                        .then(() => {{
                          this.innerText = '✅ Copiado!';
                          this.style.background = '#2e7d32';
                          setTimeout(() => {{ this.innerText = '📋 Copiar texto'; this.style.background = '#0066cc'; }}, 2200);
                        }})
                        .catch(() => alert('Use Ctrl+A / Ctrl+C na caixa acima.'));
                    "
                    style="background:#0066cc;color:#fff;border:none;padding:.55rem 1.3rem;
                           font-size:1rem;font-weight:600;border-radius:10px;cursor:pointer;">
                      📋 Copiar texto
                    </button>
                    """,
                    height=52,
                )
