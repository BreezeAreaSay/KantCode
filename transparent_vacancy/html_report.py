"""Отчёт одной HTML-страницей: список вакансий, фильтры, письма работодателю.

Страница самодостаточна: данные отчёта встроены в неё, сервер и сеть не нужны.
Текст из вакансий попадает на страницу только через ``textContent``, поэтому
разметка в описании вакансии не исполняется.
"""

from __future__ import annotations

import json
import re

from .letters import build_letter

_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Прозрачная вакансия — отчёт</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #ffffff; --text: #1c2330; --muted: #5d6778; --line: #dfe3ea;
    --accent: #1f5fbf; --gap: #b3261e; --ambiguity: #8a5a00; --contradiction: #6a3fb5; --ok: #1e7a46;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #12151b; --card: #1b2029; --text: #e6e9ef; --muted: #9aa4b5; --line: #2c3340;
      --accent: #7fb0ff; --gap: #ff8a80; --ambiguity: #f0b952; --contradiction: #b99cf5; --ok: #6fd39b;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 980px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 24px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 20px; overflow-wrap: anywhere; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
  .stat b { display: block; font-size: 24px; line-height: 1.2; overflow-wrap: anywhere; }
  .stat.model b { font-size: 17px; line-height: 1.7; }
  .stat span { color: var(--muted); font-size: 13px; }
  .filters { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin-bottom: 20px; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 10px; }
  .row:last-child { margin-bottom: 0; }
  .row > .label { color: var(--muted); font-size: 13px; width: 86px; flex: none; }
  .chip { border: 1px solid var(--line); background: transparent; color: var(--text); border-radius: 999px; padding: 4px 12px; font: inherit; font-size: 13px; cursor: pointer; }
  .chip[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--card); }
  .chip:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  input[type="search"] { flex: 1; min-width: 200px; border: 1px solid var(--line); background: var(--bg); color: var(--text); border-radius: 8px; padding: 7px 10px; font: inherit; }
  label.check { font-size: 13px; display: flex; gap: 6px; align-items: center; }
  .count { color: var(--muted); margin: 0 0 12px; }
  .vacancy { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
  .vacancy h2 { font-size: 17px; margin: 0; overflow-wrap: anywhere; }
  .vacancy .meta { color: var(--muted); font-size: 13px; margin: 2px 0 0; overflow-wrap: anywhere; }
  .vacancy a { color: var(--accent); }
  .clean { color: var(--ok); margin: 10px 0 0; font-size: 14px; }
  .finding { border-top: 1px solid var(--line); margin-top: 12px; padding-top: 12px; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; font-size: 12px; }
  .tag { border-radius: 6px; padding: 1px 8px; border: 1px solid var(--line); color: var(--muted); }
  .tag.gap { color: var(--gap); border-color: var(--gap); }
  .tag.ambiguity { color: var(--ambiguity); border-color: var(--ambiguity); }
  .tag.contradiction { color: var(--contradiction); border-color: var(--contradiction); }
  .quote { margin: 6px 0; padding: 6px 10px; border-left: 3px solid var(--line); color: var(--muted); overflow-wrap: anywhere; }
  .quote code { font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .finding p { margin: 4px 0; }
  .advice b { font-weight: 600; }
  .stat.model b { font-size: 14px; line-height: 1.4; }
  .actions { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
  .button { border: 1px solid var(--accent); background: transparent; color: var(--accent); border-radius: 8px; padding: 5px 12px; font: inherit; font-size: 13px; cursor: pointer; }
  .button.primary { background: var(--accent); color: var(--card); }
  .button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .letter { margin-top: 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); }
  .letter pre { margin: 0; padding: 12px 14px; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .letter .actions { margin: 0; padding: 0 14px 12px; align-items: center; }
  .copied { color: var(--ok); font-size: 13px; }
</style>
</head>
<body>
<main>
  <h1>Прозрачная вакансия</h1>
  <p class="sub" id="sub"></p>
  <section class="stats" id="stats" aria-label="Сводка"></section>
  <section class="filters" aria-label="Фильтры">
    <div class="row"><span class="label">Тема</span><span id="topics" class="row" style="margin:0"></span></div>
    <div class="row"><span class="label">Вид</span><span id="types" class="row" style="margin:0"></span></div>
    <div class="row"><span class="label">Кто нашёл</span><span id="sources" class="row" style="margin:0"></span></div>
    <div class="row">
      <span class="label">Поиск</span>
      <input type="search" id="search" placeholder="Должность, работодатель или ID" aria-label="Поиск по должности, работодателю или ID">
      <label class="check"><input type="checkbox" id="only"> только с замечаниями</label>
    </div>
  </section>
  <p class="count" id="count" aria-live="polite"></p>
  <div id="list"></div>
</main>
<script id="report" type="application/json">__REPORT__</script>
<script id="letters" type="application/json">__LETTERS__</script>
<script>
"use strict";
const report = JSON.parse(document.getElementById("report").textContent);
const letters = JSON.parse(document.getElementById("letters").textContent);  // письмо по позиции вакансии или null
const state = { topic: null, type: null, source: null, text: "", only: false };
const SOURCES = { rule: "правило", ai: "ИИ" };
const AI_STATUS = { ok: "разобрал все вакансии", partial: "разобрал часть вакансий", unavailable: "был недоступен", disabled: "выключен" };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function chips(holderId, key, options) {
  const holder = document.getElementById(holderId);
  for (const [value, label] of [[null, "все"], ...options]) {
    const button = el("button", "chip", label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(state[key] === value));
    button.addEventListener("click", () => {
      state[key] = value;
      for (const other of holder.children) other.setAttribute("aria-pressed", String(other === button));
      render();
    });
    holder.append(button);
  }
}

function matches(finding) {
  return (!state.topic || finding.topic === state.topic)
    && (!state.type || finding.type === state.type)
    && (!state.source || finding.detected_by === state.source);
}

function quoteBlock(field, value) {
  const block = el("div", "quote");
  block.append(el("code", "", field), document.createTextNode(value ? ": «" + value + "»" : ": сведений нет"));
  return block;
}

function findingNode(finding) {
  const node = el("div", "finding");
  const tags = el("div", "tags");
  tags.append(el("span", "tag", finding.topic_title), el("span", "tag " + finding.type, finding.type_title),
    el("span", "tag", "нашёл: " + (SOURCES[finding.detected_by] || finding.detected_by)));
  node.append(tags);
  if (finding.type === "contradiction" && Array.isArray(finding.conflict)) {
    for (const side of finding.conflict) node.append(quoteBlock(side.field, side.value));
  } else {
    node.append(quoteBlock(finding.field, finding.quote));
  }
  node.append(el("p", "", finding.problem));
  const advice = el("p", "advice");
  advice.append(el("b", "", "Что уточнить: "), document.createTextNode(finding.recommendation));
  node.append(advice);
  return node;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {  // file:// и старые браузеры: копируем через временное поле
    const area = el("textarea");
    area.value = text;
    area.style.position = "fixed"; area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const done = document.execCommand("copy");
    area.remove();
    return done;
  }
}

function letterNode(text) {
  const box = el("div", "letter");
  box.hidden = true;
  box.append(el("pre", "", text));
  const actions = el("div", "actions");
  const copy = el("button", "button primary", "Скопировать письмо");
  copy.type = "button";
  const note = el("span", "copied");
  note.setAttribute("aria-live", "polite");
  copy.addEventListener("click", async () => {
    note.textContent = (await copyText(text)) ? "Скопировано" : "Не удалось скопировать: выделите текст вручную";
  });
  actions.append(copy, note);
  box.append(actions);
  return box;
}

function render() {
  const list = document.getElementById("list");
  list.replaceChildren();
  const filtering = state.topic || state.type || state.source;
  const needle = state.text.trim().toLowerCase();
  let shown = 0, findings = 0;
  report.vacancies.forEach((vacancy, position) => {
    const visible = vacancy.findings.filter(matches);
    if ((filtering || state.only) && !visible.length) return;
    const haystack = [vacancy.job_name, vacancy.company, vacancy.id].join(" ").toLowerCase();
    if (needle && !haystack.includes(needle)) return;
    shown += 1; findings += visible.length;
    const card = el("article", "vacancy");
    card.append(el("h2", "", vacancy.job_name || "Без названия"));
    const meta = el("p", "meta", [vacancy.company, "ID " + (vacancy.id || "не указан")].filter(Boolean).join(" · "));
    if (vacancy.url && /^https?:\\/\\//.test(vacancy.url)) {
      const link = el("a", "", "карточка на портале");
      link.href = vacancy.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      meta.append(" · ", link);
    }
    if (!vacancy.ai_reviewed) meta.append(" · без разбора ИИ");
    card.append(meta);
    if (!vacancy.findings.length) card.append(el("p", "clean", "Замечаний нет."));
    for (const finding of visible) card.append(findingNode(finding));
    const letter = letters[position];
    if (letter) {
      const box = letterNode(letter);
      const actions = el("div", "actions");
      const toggle = el("button", "button", "Письмо работодателю");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      toggle.addEventListener("click", () => {
        box.hidden = !box.hidden;
        toggle.setAttribute("aria-expanded", String(!box.hidden));
        toggle.textContent = box.hidden ? "Письмо работодателю" : "Скрыть письмо";
      });
      actions.append(toggle);
      card.append(actions, box);
    }
    list.append(card);
  });
  document.getElementById("count").textContent = "Показано вакансий: " + shown + ", замечаний: " + findings;
}

const meta = report.meta || {};
const ai = meta.ai || {};
document.getElementById("sub").textContent = "Отчёт от " + (meta.generated_at || "—") + " · источник: " + (meta.source || "—");
for (const [value, label] of [
  [meta.vacancies, "вакансий в отчёте"],
  [meta.vacancies_with_findings, "с замечаниями"],
  [meta.findings_total, "замечаний всего"],
  [ai.findings, "из них нашёл ИИ"],
]) {
  const stat = el("div", "stat");
  stat.append(el("b", "", String(value ?? 0)), el("span", "", label));
  document.getElementById("stats").append(stat);
}
const aiStat = el("div", "stat model");
aiStat.append(el("b", "", ai.model || "—"), el("span", "", "ИИ " + (AI_STATUS[ai.status] || ai.status || "—")));
document.getElementById("stats").append(aiStat);

const topicTitles = new Map(), typeTitles = new Map();
for (const vacancy of report.vacancies) for (const finding of vacancy.findings) {
  topicTitles.set(finding.topic, finding.topic_title);
  typeTitles.set(finding.type, finding.type_title);
}
chips("topics", "topic", [...topicTitles]);
chips("types", "type", [...typeTitles]);
chips("sources", "source", Object.entries(SOURCES));
document.getElementById("search").addEventListener("input", (event) => { state.text = event.target.value; render(); });
document.getElementById("only").addEventListener("change", (event) => { state.only = event.target.checked; render(); });
render();
</script>
</body>
</html>
"""


def _plain(letter: str | None) -> str | None:
    """Письмо без разметки Markdown: его вставляют в почту как есть."""
    if letter is None:
        return None
    return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"^# ", "", letter, flags=re.M)).strip() + "\n"


def _embed(value) -> str:
    # Данные лежат внутри <script>: закрывающий тег и комментарий в тексте вакансии не должны его оборвать.
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


def render_html(report: dict) -> str:
    """HTML-страница с встроенным отчётом и письмами работодателю по нему."""
    letters = [_plain(build_letter(entry)) if isinstance(entry, dict) else None for entry in report.get("vacancies") or []]
    return _PAGE.replace("__LETTERS__", _embed(letters)).replace("__REPORT__", _embed(report))
