# -*- coding: utf-8 -*-
import itertools
import time
import base64
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

# Número máximo de paradas
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
# Geocoding (Pelotas)
# =========================
@st.cache_data(show_spinner=False, ttl=24 * 3600)
def geocode_pelotas(address: str) -> Optional[Tuple[float, float]]:
    params = {
        "q": f"{address}, Pelotas, RS, Brasil",
        "format": "json",
        "limit": 1,
    }
    headers = {"User-Agent": "rota-visita-ufpel/1.0 (educational)"}
    try:
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=(4, 20))
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
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
    """TSP por brute force. Índice 0 = UBS; 1..n = paradas."""
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


def google_maps_link(start_lat: float, start_lon: float, waypoints: List[Tuple[float, float]], mode: str) -> str:
    origin = quote_plus(f"{start_lat},{start_lon}")
    destination = origin
    wp = "|".join([f"{lat},{lon}" for (lat, lon) in waypoints])
    wp_q = quote_plus(wp) if wp else ""
    base = "https://www.google.com/maps/dir/?api=1"
    if wp_q:
        return f"{base}&origin={origin}&destination={destination}&travelmode={mode}&waypoints={wp_q}"
    return f"{base}&origin={origin}&destination={destination}&travelmode={mode}"


def fmt_time(seconds: float) -> str:
    if seconds == float("inf"):
        return "—"
    m = int(round(seconds / 60))
    h = m // 60
    mm = m % 60
    if h > 0:
        return f"{h}h {mm}min"
    return f"{mm}min"


def fmt_dist(meters: float) -> str:
    if meters == float("inf"):
        return "—"
    return f"{meters / 1000.0:.2f} km"


# =========================
# UI
# =========================
st.set_page_config(page_title="Rota de Visitas (Pelotas)", layout="centered")

st.markdown(
    """
    <style>
      header {visibility: hidden;}
      [data-testid="stToolbar"] {visibility: hidden;}
      [data-testid="stHeader"] {display:none;}
      [data-testid="stDecoration"] {display:none;}
      #MainMenu {visibility: hidden;}
      footer {visibility: hidden;}

      .block-container {
        padding-top: 0.25rem !important;
        padding-bottom: 1.2rem !important;
      }
      h1 { font-size: 1.6rem !important; margin-top: 0.2rem !important; }

      .section-title {
        font-size: 1.4rem !important;
        font-weight: 700;
        color: #222;
        margin: 0.6rem 0 0.3rem 0;
      }

      .stTabs [data-baseweb="tab"] button {
        font-size: 1.35rem !important;
        font-weight: 700 !important;
      }

      .stop-title {
        font-size: 1.45rem;
        font-weight: 700;
        margin: 0.2rem 0 0.6rem 0;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Banner
banner_path = Path(__file__).parent / "assets" / "banner.pdf.a4.png"
if banner_path.exists():
    st.image(str(banner_path), use_container_width=True)
else:
    st.caption(f"Banner não encontrado: {banner_path}")

st.markdown(
    "<h2 style='text-align:center; margin:0.25rem 0 0.25rem 0;'>🗺️ Rota de Visitas</h2>",
    unsafe_allow_html=True,
)

# Logos / links
insta_path = Path(__file__).parent / "assets" / "instagram.png"
enf_path = Path(__file__).parent / "assets" / "logo.enfermagem.png"

try:
    insta_b64 = base64.b64encode(insta_path.read_bytes()).decode()
    enf_b64 = base64.b64encode(enf_path.read_bytes()).decode()
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; gap:12px; margin-top:-10px; margin-bottom:12px;">
            <img src="data:image/png;base64,{insta_b64}" width="24">
            <a href="https://www.instagram.com/amorapele_ufpel/" target="_blank"
               style="text-decoration:none; font-weight:500;">Amor à Pele</a>
            <span>|</span>
            <a href="https://www.instagram.com/g10petsaude/" target="_blank"
               style="text-decoration:none; font-weight:500;">PET G10</a>
            <span>|</span>
            <img src="data:image/png;base64,{enf_b64}" width="24">
            <a href="https://wp.ufpel.edu.br/fen/" target="_blank"
               style="text-decoration:none; font-weight:500;">Faculdade de Enfermagem – UFPel</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
except Exception:
    pass

st.caption(f"UBS → até {MAX_STOPS} endereços → UBS (otimizado por tempo).")

with st.expander("Como funciona", expanded=False):
    st.markdown(
        f"- Você pode preencher até **{MAX_STOPS} paradas**.\n"
        "- O endereço **precisa ser confirmado** antes de entrar no cálculo.\n"
        "- A rota é otimizada por **tempo** (OSRM), fechando o ciclo na UBS.\n"
        "- No final você abre a rota no **Google Maps** e pode copiar o texto para WhatsApp."
    )

colA, colB = st.columns([1, 2])
with colA:
    ubs_name = st.selectbox("UBS (ponto de partida e retorno)", list(UBS_PELOTAS.keys()))
    mode = st.radio(
        "Modo",
        ["driving", "walking"],
        format_func=lambda x: "Carro" if x == "driving" else "A pé",
        horizontal=True,
    )
    st.caption("Dica: 'A pé' fica melhor para endereços próximos. Em visita real, carro é o padrão.")

start_lat, start_lon = UBS_PELOTAS[ubs_name]

# Estado da sessão para MAX_STOPS paradas
for i in range(1, MAX_STOPS + 1):
    st.session_state.setdefault(f"addr_{i}", "")
    st.session_state.setdefault(f"confirmed_{i}", False)

st.divider()

st.markdown("<div class='section-title'>1) Endereços (até 8)</div>", unsafe_allow_html=True)

tabs = st.tabs([f"Parada {i}" for i in range(1, MAX_STOPS + 1)])

for i, tab in enumerate(tabs, start=1):
    with tab:
        st.markdown(f"<div class='stop-title'>Parada {i}</div>", unsafe_allow_html=True)

        st.text_input(
            "Endereço — obrigatório para entrar na rota",
            key=f"addr_{i}",
            placeholder="Ex.: Rua General Osório, 100 — Centro",
        )

        st.checkbox("✅ Confirmo que o endereço está correto", key=f"confirmed_{i}")

st.divider()

st.markdown("<div class='section-title'>2) Otimizar rota e abrir no Google Maps</div>", unsafe_allow_html=True)

btn = st.button("🚀 Calcular melhor rota (UBS → paradas → UBS)")

if btn:
    raw = []
    for i in range(1, MAX_STOPS + 1):
        addr = (st.session_state.get(f"addr_{i}", "") or "").strip()
        conf = bool(st.session_state.get(f"confirmed_{i}", False))
        if addr and conf:
            raw.append((i, addr))
        elif addr and not conf:
            st.warning(f"Parada {i}: tem endereço, mas NÃO está confirmada — não entra no cálculo.")
        elif conf and not addr:
            st.warning(f"Parada {i}: marcada como confirmada, mas sem endereço — ignorada.")

    if not raw:
        st.error("Nenhuma parada confirmada. Confirme pelo menos 1 endereço.")
        st.stop()

    with st.spinner("Geocodificando endereços (Pelotas)..."):
        stops: List[Stop] = []
        for i, addr in raw:
            loc = geocode_pelotas(addr)
            if loc is None:
                st.error(f"Não encontrei: Parada {i} — '{addr}'. Tente simplificar o texto.")
                st.stop()
            lat, lon = loc
            stops.append(Stop(label=f"Parada {i}", address=addr, lat=lat, lon=lon))
            time.sleep(0.2)

    points = [(start_lat, start_lon)] + [(s.lat, s.lon) for s in stops]

    with st.spinner("Buscando matriz de tempos/distâncias (OSRM) e otimizando..."):
        try:
            durations, distances = osrm_table(mode, points)
        except Exception as e:
            st.error(f"Falha ao obter matriz de rotas (OSRM): {e}")
            st.stop()

        order, best_sec = best_cycle_order(durations)

        total_m = 0.0
        for a, b in zip(order[:-1], order[1:]):
            total_m += float(distances[a][b])

    st.success("Rota ótima encontrada (por tempo):")

    visit_indices = [idx for idx in order if idx != 0]
    ordered_stops = [stops[idx - 1] for idx in visit_indices]

    # Mostrar rota formatada (Markdown)
    seq_lines = [f"**UBS ({ubs_name})**"]
    for s in ordered_stops:
        seq_lines.append(f"→ **{s.label}**: {s.address}")
    seq_lines.append(f"→ **UBS ({ubs_name})**")
    st.markdown("\n\n".join(seq_lines))

    c1, c2 = st.columns(2)
    c1.metric("Tempo total estimado", fmt_time(best_sec))
    c2.metric("Distância total estimada", fmt_dist(total_m))

    # Link Google Maps
    wp_coords = [(s.lat, s.lon) for s in ordered_stops]
    gmaps = google_maps_link(start_lat, start_lon, wp_coords, mode)
    st.link_button("📍 Abrir rota no Google Maps", gmaps)
    st.caption("Se o Maps tentar reordenar, desative 'otimizar paradas' no app do Maps para manter a ordem.")

    # Texto UTF-8 para copy-paste (WhatsApp etc.)
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
    st.markdown("<div class='section-title'>📋 Texto para copiar (WhatsApp, e-mail etc.)</div>", unsafe_allow_html=True)
    st.text_area(
        label="Selecione tudo e copie (Ctrl+A → Ctrl+C):",
        value=rota_txt,
        height=250,
        key="rota_txt_output",
    )

    # Botão de copiar via JavaScript
    import streamlit.components.v1 as components
    rota_txt_escaped = rota_txt.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    components.html(
        f"""
        <button onclick="
            navigator.clipboard.writeText(`{rota_txt_escaped}`)
                .then(() => {{
                    this.innerText = '✅ Copiado!';
                    this.style.backgroundColor = '#28a745';
                    setTimeout(() => {{
                        this.innerText = '📋 Copiar texto';
                        this.style.backgroundColor = '#0066cc';
                    }}, 2000);
                }})
                .catch(() => alert('Erro ao copiar. Use Ctrl+A / Ctrl+C na caixa acima.'));
        "
        style="
            background-color: #0066cc;
            color: white;
            border: none;
            padding: 0.5rem 1.2rem;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            margin-top: 4px;
        ">
            📋 Copiar texto
        </button>
        """,
        height=55,
    )
