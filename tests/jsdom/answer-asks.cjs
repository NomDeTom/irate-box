// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The admin pages ask on the page, not in the browser's pop-up (AW.confirmHere: a .aw-ask with the words, then
// Confirm and Cancel). A page test that answers with its own w.confirm stub keeps doing so: each question that
// appears is put to w.confirm, and its Confirm or Cancel pressed by the answer.
// Usage: require("./answer-asks.cjs")(w) once the page's window exists; w.answerAsks = false to answer by hand.
module.exports = (w) => {
  const d = w.document;
  const answer = (box) => {
    if (w.answerAsks === false || box.dataset.answered) return; // a test answering by hand sets it false
    box.dataset.answered = "1";
    const text = (box.querySelector("p") || box).textContent;
    const yes = !!w.confirm(text);
    const [ok, no] = box.querySelectorAll("button");
    (yes ? ok : no).click();
  };
  const look = (n) => {
    if (n.nodeType !== 1) return;
    if (n.matches(".aw-ask")) answer(n);
    n.querySelectorAll(".aw-ask").forEach(answer);
  };
  const start = () =>
    new w.MutationObserver((muts) => muts.forEach((m) => m.addedNodes.forEach(look))).observe(d.documentElement, {
      childList: true,
      subtree: true,
    });
  if (d.documentElement) start();
  else d.addEventListener("DOMContentLoaded", start);
};
