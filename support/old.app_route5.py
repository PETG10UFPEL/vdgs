import itertools
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import requests
import streamlit as st
from PIL import Image, ImageOps


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
     "UBS Fraget — Fragata": (-31.752495, -52.368885),
     "UBS Areal I — Areal": (-31.7547494, -52.3286805),
     "UBS CSU Areal — Areal": (-31.7553824, -52.3170841),
     "UBS CSU Cruzeiro — Areal": (-31.7636424, -52.3231341),
     "UBS Leocádia — Areal": (-31.7509573, -52.3234772),
     "UBS Bom Jesus — Areal": (-31.7460013, -52.3195169),
     "UBS Santa Terezinha — Três Vendas": (-31.7258671, -52.3495507),
     "UBS Salgado Filho — Três Vendas": (-31.7328613, -52.3366328),
     "UBS Vila Municipal — Três Vendas": (-31.7304988, -52.3268676),
     "UBS Arco Íris — Três Vendas": (-31.7201459, -52.3052027),
     "UBS Cohab Lindóia — Três Vendas": (-31.7097515, -52.34796)
}

ADDRESS_HINT = re.compile(
    r"(rua|r\.|av\.|avenida|trav\.|travessa|alameda|pra(ç|c)a|estrada|rodovia|lote|km|n[ºo]\s*\d+)",
    re.IGNORECASE,
)

OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/{profile}/{coords}"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass
class Stop:
    label: str                 # "Parada 1"
    address: str               # texto final confirmado
    lat: float
    lon: float


# =========================
# EasyOCR (carrega 1x)
# =========================
@st.cache_resource(show_spinner=False)
def get_easyocr_reader():
    import easyocr
    # pt + en ajuda quando vem “Av.”, “No”, etc.
    return easyocr.Reader(["pt", "en"], gpu=False)


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    gray = ImageOps.autocontrast(gray)
    return gray


def ocr_easy(img: Image.Image) -> str:
    reader = get_easyocr_reader()
    processed = preprocess_for_ocr(img)
    # EasyOCR espera numpy array; PIL -> convert via bytes? mais simples: converter para array
    import numpy as np
    arr = np.array(processed)
    results = reader.readtext(arr, detail=0)  # lista de strings
    text = "\n".join([r.strip() for r in results if str(r).strip()])
    return text.strip()


def pick_best_address(text: str) -> Optional[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    candidates = [ln for ln in lines if ADDRESS_HINT.search(ln)]
    if not candidates:
        # fallback: linhas "grandinhas"
        candidates = [ln for ln in lines if len(ln) >= 10]

    if not candidates:
        return None

    # pega a maior linha: regra simples e eficiente
    return max(candidates, key=len)


# =========================
# Geocoding (Pelotas)
# =========================
@st.cache_data(show_spinner=False, ttl=24 * 3600)
def geocode_pelotas(address: str) -> Optional[Tuple[float, float]]:
    """
    Geocodifica usando Nominatim. Restringe para Pelotas/RS/BR.
    Cacheado para não ficar batendo em rede.
    """
    params = {
        "q": f"{address}, Pelotas, RS, Brasil",
        "format": "json",
        "limit": 1,
    }
    headers = {
        "User-Agent": "rota-visita-ufpel/1.0 (educational)",
    }
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
# OSRM table (tempo/distância real)
# =========================
@st.cache_data(show_spinner=False, ttl=2 * 3600)
def osrm_table(profile: str, points: List[Tuple[float, float]]) -> Tuple[List[List[float]], List[List[float]]]:
    """
    Retorna matrizes (durations, distances) em segundos e metros.
    points: lista de (lat, lon)
    """
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
    """
    Resolve TSP em ciclo com brute force:
    índice 0 = UBS (origem/destino)
    índices 1..n = paradas
    Retorna: (ordem com índices dos pontos, custo total)
      Ex: [0, 2, 1, 3, 0]
    """
    n = len(cost_matrix) - 1  # paradas
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
        # volta pra UBS
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
    """
    Gera link do Google Maps:
    origin=UBS
    destination=UBS (pra fechar o ciclo)
    waypoints=lat,lon|lat,lon|...
    """
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
    km = meters / 1000.0
    return f"{km:.2f} km"


# =========================
# UI
# =========================
st.set_page_config(page_title="Rota UBS + 5 endereços (EasyOCR)", layout="wide")
st.title("🏥 Rota de Visita Domiciliar (até 5 paradas) — UBS → pacientes → UBS")

with st.expander("Como funciona", expanded=False):
    st.markdown(
        "- Você pode preencher até 5 paradas.\n"
        "- Cada parada pode vir de **imagem** (OCR com EasyOCR) ou **texto**.\n"
        "- O endereço **precisa ser confirmado**.\n"
        "- A rota é otimizada por **tempo** (OSRM), e fecha o ciclo voltando à UBS.\n"
        "- No final você abre a rota no **Google Maps**."
    )

colA, colB = st.columns([1, 2])

with colA:
    ubs_name = st.selectbox("UBS (ponto de partida e retorno)", list(UBS_PELOTAS.keys()))
    mode = st.radio("Modo", ["driving", "walking"], format_func=lambda x: "Carro" if x == "driving" else "A pé", horizontal=True)
    st.caption("Dica: 'A pé' costuma ficar melhor com endereços próximos. Em visita real, carro é o padrão.")

start_lat, start_lon = UBS_PELOTAS[ubs_name]

# Estado
for i in range(1, 6):
    st.session_state.setdefault(f"addr_{i}", "")
    st.session_state.setdefault(f"confirmed_{i}", False)
    st.session_state.setdefault(f"ocr_text_{i}", "")

st.divider()
st.subheader("1) Endereços (até 5)")

tabs = st.tabs([f"Parada {i}" for i in range(1, 6)])

for i, tab in enumerate(tabs, start=1):
    with tab:
        st.write(f"### Parada {i}")

        left, right = st.columns([1, 1])

        with left:
            up = st.file_uploader(
                f"Imagem com endereço (opcional) — Parada {i}",
                type=["png", "jpg", "jpeg"],
                key=f"img_{i}",
            )

            if up is not None:
                img = Image.open(up)

                # compatível com streamlit velho/novo
                try:
                    st.image(img, caption="Imagem enviada", use_container_width=True)
                except TypeError:
                    st.image(img, caption="Imagem enviada", use_column_width=True)

                if st.button(f"🔎 OCR (EasyOCR) — Parada {i}", key=f"btn_ocr_{i}"):
                    with st.spinner("Rodando OCR..."):
                        try:
                            text = ocr_easy(img)
                            st.session_state[f"ocr_text_{i}"] = text
                            if not text.strip():
                                st.error("Não encontrei texto na imagem. Envie outra imagem (mais nítida) ou use texto digitado.")
                            else:
                                suggested = pick_best_address(text) or ""
                                if suggested:
                                    st.session_state[f"addr_{i}"] = suggested
                                st.success("OCR concluído. Revise/ajuste e CONFIRME o endereço.")
                        except Exception as e:
                            st.error(f"OCR falhou: {e}")

        with right:
            if st.session_state.get(f"ocr_text_{i}", "").strip():
                st.write("Texto do OCR (para conferência):")
                st.code(st.session_state[f"ocr_text_{i}"])

            st.text_input(
                "Endereço final (obrigatório para entrar na rota)",
                key=f"addr_{i}",
                placeholder="Ex.: Rua General Osório, 100 — Centro",
            )

            st.checkbox("✅ Confirmo que o endereço está correto", key=f"confirmed_{i}")

st.divider()
st.subheader("2) Otimizar rota e abrir no Google Maps")

btn = st.button("🚀 Calcular melhor rota (UBS → paradas → UBS)")

if btn:
    # coletar paradas confirmadas
    raw = []
    for i in range(1, 6):
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
            # pequena pausa “educada” quando não está em cache
            loc = geocode_pelotas(addr)
            if loc is None:
                st.error(f"Não encontrei: Parada {i} — '{addr}'. Tente simplificar o texto.")
                st.stop()
            lat, lon = loc
            stops.append(Stop(label=f"Parada {i}", address=addr, lat=lat, lon=lon))
            time.sleep(0.2)

    # pontos para OSRM: 0 = UBS; 1..n = paradas
    points = [(start_lat, start_lon)] + [(s.lat, s.lon) for s in stops]

    with st.spinner("Buscando matriz de tempos/distâncias (OSRM) e otimizando..."):
        try:
            durations, distances = osrm_table(mode, points)
        except Exception as e:
            st.error(f"Falha ao obter matriz de rotas (OSRM): {e}")
            st.stop()

        order, best_sec = best_cycle_order(durations)

        # soma distâncias na ordem encontrada (em metros)
        total_m = 0.0
        for a, b in zip(order[:-1], order[1:]):
            total_m += float(distances[a][b])

    # construir saída
    st.success("Rota ótima encontrada (por tempo):")

    # ordem de paradas sem a UBS
    visit_indices = [idx for idx in order if idx != 0]
    ordered_stops = [stops[idx - 1] for idx in visit_indices]  # idx-1 porque stops começa em 1

    # mostrar sequência
    seq_lines = [f"**UBS ({ubs_name})**"]
    for s in ordered_stops:
        seq_lines.append(f"→ **{s.label}**: {s.address}")
    seq_lines.append(f"→ **UBS ({ubs_name})**")

    st.markdown("\n\n".join(seq_lines))

    c1, c2 = st.columns(2)
    c1.metric("Tempo total estimado", fmt_time(best_sec))
    c2.metric("Distância total estimada", fmt_dist(total_m))

    # Link do Google Maps com waypoints na ordem ótima (ciclo fechado)
    wp_coords = [(s.lat, s.lon) for s in ordered_stops]
    gmaps = google_maps_link(start_lat, start_lon, wp_coords, mode)

    st.link_button("📍 Abrir rota no Google Maps", gmaps)
    st.caption("Se o Maps tentar reordenar, desative 'otimizar paradas' no app do Maps para manter a ordem.")
