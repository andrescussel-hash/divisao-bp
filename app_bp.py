import re
import unicodedata
from datetime import datetime
import fitz  # PyMuPDF
import streamlit as st

st.set_page_config(
    page_title="Divisão BP - Internados",
    page_icon="🏥",
    layout="centered",
)

# Parâmetros clínicos de carga e deslocamento (v4)
PESO_PACIENTE_PADRAO = 1.0
PESO_ADMISSAO_NOVA = 1.5
PESO_INTERCONSULTA = 1.0

CUSTO_DESLOC_MIRANTE = 1.0       # +1.0 pelo deslocamento entre prédios (Paulista <-> Mirante)
CUSTO_DESLOC_TROCA_TORRE = 0.5   # +0.5 por torre adicional visitada no Paulista
CUSTO_DESLOC_TROCA_ANDAR = 0.15  # +0.15 por andar adicional na mesma torre

CORES_MEDICOS = [
    (0.78, 0.89, 0.99),  # M1: Azul
    (0.99, 0.89, 0.72),  # M2: Laranja
    (0.97, 0.95, 0.68),  # M3: Amarelo
    (0.78, 0.94, 0.80),  # M4: Verde
    (0.89, 0.82, 0.97),  # M5: Lilás
    (0.98, 0.81, 0.78),  # M6: Salmão
    (0.74, 0.92, 0.91),  # M7: Turquesa
    (0.91, 0.87, 0.80),  # M8: Areia
]


def normalizar_texto(txt: str) -> str:
    txt = txt.replace("\xa0", " ")
    nfkd = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def extrair_leito_texto(texto: str):
    texto = texto.replace("\xa0", " ")
    m_esp = re.search(r"\b(PREXA\s*\d+|1S\s*\d+|2S\s*\d+)\b", texto, re.IGNORECASE)
    if m_esp:
        return m_esp.group(1).upper()
    sem_atend = re.sub(r"\d{5,}", " ", texto)
    sem_torre_txt = re.sub(r"\btorre\s*[1-7]\b", " ", sem_atend, flags=re.IGNORECASE)
    m_leito = re.search(r"\b([1-7]\d{3})(?:[-\s]+(\d{1,2}))?\b", sem_torre_txt)
    if m_leito:
        quarto = m_leito.group(1)
        leito = m_leito.group(2) or "01"
        return f"{quarto} {leito}"
    return None


def chave_localizacao(leito: str):
    leito_clean = leito.replace("\xa0", " ").strip().upper()
    if leito_clean.startswith("1S"):
        num = int(re.sub(r"\D", "", leito_clean[2:]) or 0)
        return (1, 5, -1, num, 0)

    if "PREXA" in leito_clean or leito_clean.startswith("2S"):
        num = int(re.sub(r"\D", "", leito_clean) or 0)
        return (1, 4, -2, num, 0)

    m = re.match(r"^(\d)(\d)(\d{2})(?:[-\s]+(\d+))?$", leito_clean)
    if m:
        torre = int(m.group(1))
        andar = int(m.group(2))
        quarto = int(m.group(3))
        subleito = int(m.group(4) or 1)
        predio = 2 if torre in (6, 7) else 1
        return (predio, torre, andar, quarto, subleito)

    return (99, 99, 99, 99, 99)


def interpretar_interconsultas_whatsapp(linhas):
    torre_contexto = None
    contador_sem_leito_por_torre = {}
    resultado = []

    for linha in linhas:
        limpa = re.sub(r"[\U00010000-\U0010ffff\u200d\ufe0f\u20e3]", "", linha).replace("\xa0", " ").strip()
        limpa = re.sub(r"^\[IC\]\s*", "", limpa, flags=re.IGNORECASE).strip()
        norm = normalizar_texto(limpa)

        if "mirante" in norm and len(norm) <= 30:
            torre_contexto = 6
            continue

        m_torre_isolada = re.fullmatch(r"(?:torre\s*)?([1-7])[\s\-:.)]*", norm)
        if m_torre_isolada:
            torre_contexto = int(m_torre_isolada.group(1))
            continue

        if "sem pacientes" in norm or len(limpa) < 4:
            continue

        torre_linha = torre_contexto
        if "mirante" in norm:
            torre_linha = 6
        m_pref = re.search(r"\[?\btorre\s*([1-7])\b\]?", norm)
        if m_pref:
            torre_linha = int(m_pref.group(1))

        leito_detectado = extrair_leito_texto(limpa)
        if leito_detectado:
            loc_key = chave_localizacao(leito_detectado)
            if loc_key[1] == 4:
                torre_linha = 4
                descricao_loc = f"Leito {leito_detectado} (2º Subsolo - entre Torres 3 e 5)"
            elif leito_detectado.startswith("1S"):
                torre_linha = 5
                descricao_loc = f"Leito {leito_detectado} (PS - Torre 5)"
            else:
                torre_linha = loc_key[1]
                predio_lbl = "Mirante" if loc_key[0] == 2 else f"Torre {loc_key[1]}"
                descricao_loc = f"Leito {leito_detectado} ({predio_lbl}, {loc_key[2]}º andar)"
        elif torre_linha:
            predio = 2 if torre_linha in (6, 7) else 1
            idx_andar = contador_sem_leito_por_torre.get(torre_linha, 0) + 1
            contador_sem_leito_por_torre[torre_linha] = idx_andar
            loc_key = (predio, int(torre_linha), -10 - idx_andar, 50, 1)
            predio_lbl = "Mirante" if predio == 2 else f"Torre {torre_linha}"
            descricao_loc = f"Apenas {predio_lbl} (sem leito)"
        else:
            loc_key = (99, 99, 99, 99, 99)
            descricao_loc = "Sem localização informada"

        texto_exibicao = re.sub(r"\s+", " ", limpa).strip()
        if torre_linha in (6, 7) and "mirante" not in norm:
            texto_exibicao = f"[Mirante] {texto_exibicao}"
        elif torre_linha and torre_linha != 4 and "torre" not in norm and not leito_detectado:
            texto_exibicao = f"[Torre {torre_linha}] {texto_exibicao}"

        resultado.append({
            "texto": texto_exibicao,
            "torre": torre_linha,
            "leito": leito_detectado,
            "loc_key": loc_key,
            "descricao_loc": descricao_loc,
        })

    return resultado


def calcular_custo_deslocamento_grupo(lista_loc_keys):
    validas = [lk for lk in lista_loc_keys if lk[0] in (1, 2)]
    if not validas:
        return 0.0

    custo = 0.0
    predios = {lk[0] for lk in validas}
    if 2 in predios:
        custo += CUSTO_DESLOC_MIRANTE

    torres_reais_paulista = {lk[1] for lk in validas if lk[0] == 1 and lk[1] != 4}
    tem_prexa = any(lk[0] == 1 and lk[1] == 4 for lk in validas)

    if tem_prexa and not (3 in torres_reais_paulista or 5 in torres_reais_paulista):
        torres_efetivas = len(torres_reais_paulista) + 1
    else:
        torres_efetivas = len(torres_reais_paulista)

    if torres_efetivas > 1:
        custo += (torres_efetivas - 1) * CUSTO_DESLOC_TROCA_TORRE

    andares_reais = {(lk[0], lk[1], lk[2]) for lk in validas if lk[2] >= -2}
    if andares_reais:
        num_eixos = len({(lk[0], lk[1]) for lk in andares_reais})
        andares_extras = max(0, len(andares_reais) - num_eixos)
        custo += andares_extras * CUSTO_DESLOC_TROCA_ANDAR

    return round(custo, 2)


def calcular_carga_total_medico(grupo_pacientes):
    peso_pacs = sum(p["peso"] for p in grupo_pacientes)
    custo_desloc = calcular_custo_deslocamento_grupo([p["loc_key"] for p in grupo_pacientes])
    return peso_pacs + custo_desloc


def aplicar_peculiaridades(pacientes, peculiaridades, num_medicos):
    avisos = []
    for linha in peculiaridades:
        norm_linha = normalizar_texto(linha)
        eh_exclusao = bool(re.search(r"\b(excluir|exclui|tirar|tira|remover|remove|alta|obito|ignorar|retirar)\b", norm_linha))
        m_med = re.search(r"\b(?:m|med|medico|médico)\s*(\d+)\b", linha, re.IGNORECASE)

        if not eh_exclusao and not m_med:
            avisos.append(f"Não identifiquei qual médico ou ação na regra: '{linha}'")
            continue

        med_num = None
        if not eh_exclusao:
            med_num = int(m_med.group(1))
            if med_num < 1 or med_num > num_medicos:
                avisos.append(f"Médico M{med_num} fora do intervalo (1 a {num_medicos}) em: '{linha}'")
                continue

        resto = re.sub(r"\b(?:m|med|medico|médico)\s*\d+\b", " ", linha, flags=re.IGNORECASE)
        resto = re.sub(
            r"\b(?:excluir|exclui|tirar|tira|remover|remove|alta|óbito|obito|ignorar|retirar|deixar|colocar|ficar|com|para|o|a|os|as|paciente|pacientes|leito|leitos|pq|porque|ele|ela|quer|ver|esse|essa|esses|essas|da|do|lista)\b",
            " ",
            resto,
            flags=re.IGNORECASE,
        )
        termos = [t.strip(" :-–—>") for t in re.split(r"[,;/]|\s+e\s+", resto) if t.strip(" :-–—>")]

        for termo in termos:
            termo_norm = normalizar_texto(termo)
            if len(termo_norm) < 3:
                continue

            encontrados = []
            for p in pacientes:
                if p.get("excluido"):
                    continue
                nome_norm = normalizar_texto(p["nome"])
                leito_norm = normalizar_texto(p["leito"]).replace(" ", "")
                termo_sem_espaco = termo_norm.replace(" ", "").replace("-", "")

                if termo_norm in nome_norm or (termo_sem_espaco.isdigit() and termo_sem_espaco in leito_norm):
                    if eh_exclusao:
                        p["excluido"] = True
                    else:
                        p["medico_fixo"] = med_num
                    encontrados.append(f"{p['nome']} ({p['leito']})")

            if encontrados:
                if eh_exclusao:
                    avisos.append(f"Excluído da lista: {', '.join(encontrados)}")
                else:
                    avisos.append(f"Fixado em M{med_num}: {', '.join(encontrados)}")
            else:
                alvo_lbl = "exclusão" if eh_exclusao else f"regra M{med_num}"
                avisos.append(f"Aviso: nenhum paciente encontrado para '{termo}' ({alvo_lbl})")

    pacientes[:] = [p for p in pacientes if not p.get("excluido")]
    return avisos


def particionar_blocos_continuos_dp(itens_livres, fixos_por_medico, k, peso_extra_global=0.0):
    n = len(itens_livres)
    if n == 0:
        return [[] for _ in range(k)]

    pesos = [item["peso"] for item in itens_livres]
    prefixo_peso = [0.0] * (n + 1)
    for i in range(n):
        prefixo_peso[i + 1] = prefixo_peso[i] + pesos[i]

    peso_fixo_total = sum(sum(p["peso"] for p in g) for g in fixos_por_medico)
    custo_desloc_estimado = (k - 1) * 0.35 + (CUSTO_DESLOC_MIRANTE if any(x["loc_key"][0] == 2 for x in itens_livres) else 0.0)
    carga_ideal = (prefixo_peso[n] + peso_fixo_total + peso_extra_global + custo_desloc_estimado) / k

    dp = [[float("inf")] * (n + 1) for _ in range(k + 1)]
    corte = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0

    for j in range(1, k + 1):
        fixos_med = fixos_por_medico[j - 1]
        peso_fixo_med = sum(p["peso"] for p in fixos_med)
        locs_fixas_med = [p["loc_key"] for p in fixos_med]

        for i in range(0, n + 1):
            for p in range(0, i + 1):
                if dp[j - 1][p] == float("inf"):
                    continue

                if i == p and not fixos_med and n >= k:
                    continue

                bloco = itens_livres[p:i]
                soma_pacientes = prefixo_peso[i] - prefixo_peso[p]
                locs_bloco = locs_fixas_med + [x["loc_key"] for x in bloco]
                custo_desloc = calcular_custo_deslocamento_grupo(locs_bloco)

                carga_total_med = peso_fixo_med + soma_pacientes + custo_desloc

                penalidade_corte_andar = 0.0
                if p > 0 and i > p:
                    loc_ant = itens_livres[p - 1]["loc_key"]
                    loc_ini = itens_livres[p]["loc_key"]
                    if loc_ant[:3] == loc_ini[:3]:
                        penalidade_corte_andar = 0.25

                custo = dp[j - 1][p] + ((carga_total_med - carga_ideal) ** 2) * 2.0 + penalidade_corte_andar
                if custo < dp[j][i]:
                    dp[j][i] = custo
                    corte[j][i] = p

    grupos = []
    idx = n
    for j in range(k, 0, -1):
        prev = corte[j][idx]
        grupos.append(itens_livres[prev:idx])
        idx = prev
    grupos.reverse()
    return grupos


def escolher_medico_interconsulta(item_ic, grupos_finais):
    loc_ic = item_ic["loc_key"]
    torre_ic = item_ic["torre"]

    def tem_na_lista_original(grupo, pred):
        return any(p.get("origem") == "PDF" and pred(p["loc_key"]) for p in grupo)

    if loc_ic[0] in (1, 2):
        predio_ic, eixo_torre, andar_ic, quarto_ic, _ = loc_ic

        if predio_ic == 2:
            titulares_mirante = [i for i, g in enumerate(grupos_finais) if tem_na_lista_original(g, lambda lk: lk[0] == 2)]
            if titulares_mirante:
                return min(titulares_mirante, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

        if eixo_torre == 4:
            no_eixo_3_5 = [
                i for i, g in enumerate(grupos_finais)
                if tem_na_lista_original(g, lambda lk: lk[0] == 1 and lk[1] in (3, 4, 5))
            ]
            if no_eixo_3_5:
                return min(no_eixo_3_5, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

        if quarto_ic != 50 and andar_ic >= 0:
            mesmo_quarto = [
                i for i, g in enumerate(grupos_finais)
                if tem_na_lista_original(g, lambda lk: lk[:4] == (predio_ic, eixo_torre, andar_ic, quarto_ic))
            ]
            if mesmo_quarto:
                return min(mesmo_quarto, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

            mesmo_andar = [
                i for i, g in enumerate(grupos_finais)
                if tem_na_lista_original(g, lambda lk: lk[:3] == (predio_ic, eixo_torre, andar_ic))
            ]
            if mesmo_andar:
                return min(mesmo_andar, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

        mesma_torre = [
            i for i, g in enumerate(grupos_finais)
            if tem_na_lista_original(g, lambda lk: lk[:2] == (predio_ic, eixo_torre))
        ]
        if mesma_torre:
            return min(mesma_torre, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

    elif torre_ic:
        predio_ic = 2 if torre_ic in (6, 7) else 1
        if predio_ic == 2:
            titulares_mirante = [i for i, g in enumerate(grupos_finais) if tem_na_lista_original(g, lambda lk: lk[0] == 2)]
            if titulares_mirante:
                return min(titulares_mirante, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

        mesma_torre = [
            i for i, g in enumerate(grupos_finais)
            if tem_na_lista_original(g, lambda lk: lk[:2] == (predio_ic, int(torre_ic)))
        ]
        if mesma_torre:
            return min(mesma_torre, key=lambda i: calcular_carga_total_medico(grupos_finais[i]))

    return min(range(len(grupos_finais)), key=lambda i: calcular_carga_total_medico(grupos_finais[i]))


def rebalancear_fronteiras_finais(grupos_finais):
    k = len(grupos_finais)
    if k < 2:
        return

    def objetivo_global():
        cargas = [calcular_carga_total_medico(g) for g in grupos_finais]
        media = sum(cargas) / k
        variancia = sum((c - media) ** 2 for c in cargas)
        amplitude = max(cargas) - min(cargas)
        return variancia + 0.5 * (amplitude ** 2)

    for _ in range(60):
        melhor_obj = objetivo_global()
        melhor_cadeia = None

        for b in range(1, k):
            for a in range(0, b):
                movidos = []
                valido = True
                for step in range(b, a, -1):
                    moveis = [
                        p for p in grupos_finais[step]
                        if p.get("origem") == "PDF" and p.get("medico_fixo") is None
                    ]
                    if len(moveis) <= 1:
                        valido = False
                        break
                    cand = moveis[0]
                    grupos_finais[step].remove(cand)
                    grupos_finais[step - 1].append(cand)
                    cand["medico"] = step
                    movidos.append((step, cand))

                if valido:
                    obj = objetivo_global()
                    if obj < melhor_obj - 0.02:
                        melhor_obj = obj
                        melhor_cadeia = ("esq", a, b)

                for step, cand in reversed(movidos):
                    grupos_finais[step - 1].remove(cand)
                    grupos_finais[step].insert(0, cand)
                    cand["medico"] = step + 1

        for a in range(0, k - 1):
            for b in range(a + 1, k):
                movidos = []
                valido = True
                for step in range(a, b):
                    moveis = [
                        p for p in grupos_finais[step]
                        if p.get("origem") == "PDF" and p.get("medico_fixo") is None
                    ]
                    if len(moveis) <= 1:
                        valido = False
                        break
                    cand = moveis[-1]
                    grupos_finais[step].remove(cand)
                    grupos_finais[step + 1].insert(0, cand)
                    cand["medico"] = step + 2
                    movidos.append((step, cand))

                if valido:
                    obj = objetivo_global()
                    if obj < melhor_obj - 0.02:
                        melhor_obj = obj
                        melhor_cadeia = ("dir", a, b)

                for step, cand in reversed(movidos):
                    grupos_finais[step + 1].remove(cand)
                    grupos_finais[step].append(cand)
                    cand["medico"] = step + 1

        if not melhor_cadeia:
            break

        direcao, a, b = melhor_cadeia
        if direcao == "esq":
            for step in range(b, a, -1):
                moveis = [
                    p for p in grupos_finais[step]
                    if p.get("origem") == "PDF" and p.get("medico_fixo") is None
                ]
                cand = moveis[0]
                grupos_finais[step].remove(cand)
                grupos_finais[step - 1].append(cand)
                cand["medico"] = step
        else:
            for step in range(a, b):
                moveis = [
                    p for p in grupos_finais[step]
                    if p.get("origem") == "PDF" and p.get("medico_fixo") is None
                ]
                cand = moveis[-1]
                grupos_finais[step].remove(cand)
                grupos_finais[step + 1].insert(0, cand)
                cand["medico"] = step + 2


def extrair_dados_pdf(doc):
    page = doc[0]
    palavras = page.get_text("words")
    texto_completo = page.get_text("text")

    m_data = re.search(r"Impresso em:\s*(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2}:\d{2})", texto_completo)
    rodape_info = m_data.group(0) if m_data else f"Impresso em: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    data_doc = rodape_info.split()[2] if len(rodape_info.split()) >= 3 else datetime.now().strftime("%d/%m/%Y")

    m_crm = re.search(r"(CRM\d+\s+CATE\d+)", texto_completo)
    crm_info = m_crm.group(1) if m_crm else "CATE1092"

    linhas_agrupadas = []
    palavras_ordenadas = sorted(palavras, key=lambda w: (round(w[1], 1), w[0]))

    for w in palavras_ordenadas:
        x0, y0, x1, y1, _ = w[:5]
        if y0 < 85 or y0 > 775:
            continue

        adicionado = False
        for linha in linhas_agrupadas:
            if abs(linha["y_centro"] - ((y0 + y1) / 2)) < 3.5:
                linha["palavras"].append(w)
                linha["y0"] = min(linha["y0"], y0)
                linha["y1"] = max(linha["y1"], y1)
                adicionado = True
                break
        if not adicionado:
            linhas_agrupadas.append({
                "y0": y0,
                "y1": y1,
                "y_centro": (y0 + y1) / 2,
                "palavras": [w],
            })

    pacientes = []
    em_cc = []

    for linha in linhas_agrupadas:
        ws = sorted(linha["palavras"], key=lambda w: w[0])
        nome = " ".join(w[4] for w in ws if w[0] < 192)
        atendimento = " ".join(w[4] for w in ws if 192 <= w[0] < 430)
        bloco_dir = " ".join(w[4] for w in ws if w[0] >= 430)

        if not nome or not bloco_dir:
            continue

        m_adm = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", bloco_dir)
        admissao = m_adm.group(1) if m_adm else ""
        leito = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", bloco_dir).strip()
        leito = re.sub(r"\s+", " ", leito)

        if not leito:
            continue

        if leito.upper().startswith("CC"):
            em_cc.append({"nome": nome, "atendimento": atendimento, "admissao": admissao, "leito": leito})
            continue

        loc_key = chave_localizacao(leito)
        eh_novo = (admissao.strip() == data_doc)
        peso = PESO_ADMISSAO_NOVA if eh_novo else PESO_PACIENTE_PADRAO

        pacientes.append({
            "origem": "PDF",
            "nome": nome,
            "atendimento": atendimento,
            "admissao": admissao,
            "leito": leito,
            "loc_key": loc_key,
            "peso": peso,
            "novo": eh_novo,
            "medico_fixo": None,
        })

    return pacientes, em_cc, data_doc, rodape_info, crm_info


def processar_divisao(pdf_bytes, num_medicos, peculiaridades, interconsultas, admissoes_extras):
    doc_orig = fitz.open(stream=pdf_bytes, filetype="pdf")
    pacientes, em_cc, data_doc, rodape_info, crm_info = extrair_dados_pdf(doc_orig)

    avisos_fixos = aplicar_peculiaridades(pacientes, peculiaridades, num_medicos)

    fixos_por_medico = [[] for _ in range(num_medicos)]
    pacientes_livres = []

    for p in pacientes:
        if p["medico_fixo"] is not None:
            idx = p["medico_fixo"] - 1
            p["medico"] = p["medico_fixo"]
            fixos_por_medico[idx].append(p)
        else:
            pacientes_livres.append(p)

    pacientes_livres.sort(key=lambda p: p["loc_key"])
    peso_extra_global = len(interconsultas) * PESO_INTERCONSULTA + len(admissoes_extras) * PESO_ADMISSAO_NOVA

    grupos_livres = particionar_blocos_continuos_dp(
        pacientes_livres,
        fixos_por_medico,
        num_medicos,
        peso_extra_global=peso_extra_global,
    )

    grupos_finais = [[] for _ in range(num_medicos)]
    for idx_med in range(num_medicos):
        for p in fixos_por_medico[idx_med]:
            grupos_finais[idx_med].append(p)
        for p in grupos_livres[idx_med]:
            p["medico"] = idx_med + 1
            grupos_finais[idx_med].append(p)

    ics_alocadas = []
    for ic in interconsultas:
        idx_med = escolher_medico_interconsulta(ic, grupos_finais)
        grupos_finais[idx_med].append({
            "origem": "IC",
            "nome": ic["texto"],
            "loc_key": ic["loc_key"],
            "peso": PESO_INTERCONSULTA,
        })
        ics_alocadas.append((ic["texto"], idx_med))

    adms_alocadas = []
    for adm in admissoes_extras:
        idx_med = min(range(num_medicos), key=lambda i: calcular_carga_total_medico(grupos_finais[i]))
        grupos_finais[idx_med].append({
            "origem": "ADM",
            "nome": adm,
            "loc_key": (99, 99, 99, 99, 99),
            "peso": PESO_ADMISSAO_NOVA,
        })
        adms_alocadas.append((adm, idx_med))

    rebalancear_fronteiras_finais(grupos_finais)

    todas_linhas_tabela = []
    for p_cc in em_cc:
        todas_linhas_tabela.append({"tipo": "CC", **p_cc})
    for p in sorted(pacientes, key=lambda x: x["loc_key"]):
        todas_linhas_tabela.append({"tipo": "ATIVO", **p})

    largura = 595.0
    altura_base = 842.0
    altura_linha = 12.6
    y_inicio_tabela = 86.0
    altura_tabela = len(todas_linhas_tabela) * altura_linha

    bloco_extras = 40
    if ics_alocadas:
        bloco_extras += 18 + len(ics_alocadas) * 16.5
    if adms_alocadas:
        bloco_extras += 18 + len(adms_alocadas) * 16.5

    altura_necessaria = max(altura_base, y_inicio_tabela + altura_tabela + bloco_extras + 45)

    doc = fitz.open()
    page = doc.new_page(width=largura, height=altura_necessaria)

    page.draw_rect(fitz.Rect(18, 16, largura - 18, altura_necessaria - 16), color=(0.35, 0.35, 0.35), width=0.8)

    page.insert_text(
        (132, 46),
        "BP - Pacientes Internados (ocupação hospitalar)",
        fontsize=14.5,
        fontname="hebo",
        color=(0.05, 0.05, 0.05),
    )

    y_cab = 72.0
    page.draw_rect(fitz.Rect(24, y_cab, largura - 24, y_cab + 13), color=(0.4, 0.4, 0.4), fill=(0.93, 0.93, 0.93), width=0.5)
    page.insert_text((28, y_cab + 9.5), "Paciente", fontsize=8.2, fontname="hebo")
    page.insert_text((200, y_cab + 9.5), "Atendimento Médico", fontsize=8.2, fontname="hebo")
    page.insert_text((422, y_cab + 9.5), "Admissão", fontsize=8.2, fontname="hebo")
    page.insert_text((484, y_cab + 9.5), "UI / Leito", fontsize=8.2, fontname="hebo")
    page.insert_text((544, y_cab + 9.5), "Méd", fontsize=8.2, fontname="hebo")

    y = y_inicio_tabela
    for item in todas_linhas_tabela:
        row_rect = fitz.Rect(24, y, largura - 24, y + altura_linha)

        if item["tipo"] == "CC":
            page.draw_rect(row_rect, color=(0.82, 0.82, 0.82), fill=(0.92, 0.92, 0.92), width=0.3)
            cor_txt = (0.45, 0.45, 0.45)
            label_med = "CC"
            fill_badge = (0.96, 0.96, 0.96)
        else:
            med = item["medico"]
            cor_fundo = CORES_MEDICOS[(med - 1) % len(CORES_MEDICOS)]
            page.draw_rect(row_rect, color=(0.82, 0.82, 0.82), fill=cor_fundo, width=0.3)
            cor_txt = (0.05, 0.05, 0.05)
            label_med = f"M{med}*" if item.get("medico_fixo") else f"M{med}"
            fill_badge = (1, 1, 1)

        y_txt = y + 9.3
        page.insert_text((28, y_txt), item["nome"][:34], fontsize=8.0, fontname="helv", color=cor_txt)
        page.insert_text((200, y_txt), item["atendimento"][:40], fontsize=8.0, fontname="helv", color=cor_txt)
        page.insert_text((422, y_txt), item["admissao"], fontsize=8.0, fontname="helv", color=cor_txt)
        page.insert_text((484, y_txt), item["leito"], fontsize=8.0, fontname="hebo", color=cor_txt)

        badge = fitz.Rect(541, y + 1.0, largura - 26, y + altura_linha - 1.0)
        page.draw_rect(badge, color=(0.25, 0.25, 0.25), fill=fill_badge, width=0.6)
        x_badge = 545.5 if "*" in label_med else 548.5
        page.insert_text((x_badge, y_txt), label_med, fontsize=8.0, fontname="hebo", color=(0.05, 0.05, 0.05))

        y += altura_linha

    y_cursor = y + 12

    def desenhar_item_extra(texto_item, idx_med, tag_tipo):
        nonlocal y_cursor
        med = idx_med + 1
        cor = CORES_MEDICOS[(med - 1) % len(CORES_MEDICOS)]

        box = fitz.Rect(24, y_cursor, largura - 24, y_cursor + 14.5)
        page.draw_rect(box, color=(0.55, 0.55, 0.55), fill=cor, width=0.5)

        badge = fitz.Rect(541, y_cursor + 1.2, largura - 26, y_cursor + 13.3)
        page.draw_rect(badge, color=(0.25, 0.25, 0.25), fill=(1, 1, 1), width=0.6)
        page.insert_text((548.5, y_cursor + 10.2), f"M{med}", fontsize=8.0, fontname="hebo", color=(0.05, 0.05, 0.05))

        page.insert_text(
            (28, y_cursor + 10.5),
            f"[{tag_tipo}] {texto_item}",
            fontsize=8.2,
            fontname="hebo",
            color=(0.05, 0.05, 0.05),
        )
        y_cursor += 16.5

    if ics_alocadas:
        page.insert_text((24, y_cursor + 8), "INTERCONSULTAS:", fontsize=8.5, fontname="hebo")
        y_cursor += 12
        for texto_ic, idx_med in ics_alocadas:
            desenhar_item_extra(texto_ic, idx_med, "IC")

    if adms_alocadas:
        y_cursor += 4
        page.insert_text((24, y_cursor + 8), "ADMISSÕES EXTRAS:", fontsize=8.5, fontname="hebo")
        y_cursor += 12
        for texto_adm, idx_med in adms_alocadas:
            desenhar_item_extra(texto_adm, idx_med, "ADM")

    contagem_total = [len(g) for g in grupos_finais]
    y_rodape_base = altura_necessaria - 34
    resumo_txt = "Total por médico: " + " | ".join(
        f"M{i+1}: {contagem_total[i]}" for i in range(num_medicos)
    )
    page.insert_text((24, y_rodape_base), resumo_txt, fontsize=8.5, fontname="hebo", color=(0.05, 0.05, 0.05))

    y_linha_rodape = altura_necessaria - 22
    page.draw_line((24, y_linha_rodape - 9), (largura - 24, y_linha_rodape - 9), color=(0.4, 0.4, 0.4), width=0.6)
    page.insert_text((24, y_linha_rodape), rodape_info, fontsize=7.5, fontname="helv", color=(0.25, 0.25, 0.25))
    page.insert_text((235, y_linha_rodape), "Página 1", fontsize=7.5, fontname="helv", color=(0.25, 0.25, 0.25))
    page.insert_text((455, y_linha_rodape), crm_info, fontsize=7.5, fontname="helv", color=(0.25, 0.25, 0.25))

    pdf_out = doc.tobytes()
    png_out = page.get_pixmap(dpi=250).tobytes("png")

    tabela_cargas = []
    for i in range(num_medicos):
        c_total = calcular_carga_total_medico(grupos_finais[i])
        c_desloc = calcular_custo_deslocamento_grupo([p["loc_key"] for p in grupos_finais[i]])
        tabela_cargas.append({
            "Médico": f"M{i+1}",
            "Pacientes": contagem_total[i],
            "Deslocamento": f"+{c_desloc:.2f}",
            "Carga Total": round(c_total, 2),
        })

    return pdf_out, png_out, resumo_txt, tabela_cargas, avisos_fixos


# --- Interface Web (Streamlit) ---
st.title("🏥 Divisão de Internados - BP")
st.caption("Distribuição geográfica por torre, andar e carga de deslocamento.")

arquivo_pdf = st.file_uploader("1. Envie o PDF da lista de internados", type=["pdf"])
num_medicos = st.number_input("2. Número de médicos hoje", min_value=2, max_value=12, value=7, step=1)

peculiaridades_txt = st.text_area(
    "3. Peculiaridades / Fixos / Exclusões do dia (opcional)",
    placeholder="Ex: M3: Paciente A, Paciente B\nExcluir: Paciente C, 1310 01",
    height=85,
)

interconsultas_txt = st.text_area(
    "4. Interconsultas (cole direto do WhatsApp)",
    placeholder="Cole aqui o texto com emojis e torres...",
    height=140,
)

linhas_ic_raw = [l.strip() for l in interconsultas_txt.splitlines() if l.strip()]
interconsultas_parsed = interpretar_interconsultas_whatsapp(linhas_ic_raw)

if interconsultas_parsed:
    with st.expander(f"🔎 Prévia da leitura das interconsultas ({len(interconsultas_parsed)} detectadas)", expanded=True):
        for idx, ic in enumerate(interconsultas_parsed, start=1):
            st.markdown(f"**{idx}.** `{ic['texto']}` → {ic['descricao_loc']}")

admissoes_txt = st.text_area(
    "5. Admissões extras que ainda não subiram no sistema (opcional)",
    placeholder="Ex: Paciente Exemplo - Equipe",
    height=75,
)

if st.button("Gerar Divisão", type="primary", use_container_width=True):
    if not arquivo_pdf:
        st.error("Por favor, envie o arquivo PDF da lista primeiro.")
    else:
        peculiaridades = [l.strip() for l in peculiaridades_txt.splitlines() if l.strip()]
        admissoes_extras = [l.strip() for l in admissoes_txt.splitlines() if l.strip()]

        pdf_out, png_out, resumo_txt, tabela_cargas, avisos_fixos = processar_divisao(
            arquivo_pdf.read(),
            int(num_medicos),
            peculiaridades,
            interconsultas_parsed,
            admissoes_extras,
        )

        if avisos_fixos:
            for av in avisos_fixos:
                st.info(av)

        st.success(resumo_txt)
        st.image(png_out, caption="Lista Dividida - Pronta para compartilhar", use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "📥 Baixar Imagem (PNG)",
                data=png_out,
                file_name="lista_dividida.png",
                mime="image/png",
                use_container_width=True,
            )
        with col2:
            st.download_button(
                "📄 Baixar PDF",
                data=pdf_out,
                file_name="lista_dividida.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

        with st.expander("📊 Detalhamento de carga e deslocamento por médico"):
            st.table(tabela_cargas)
