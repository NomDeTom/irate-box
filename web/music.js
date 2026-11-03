// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box's music: the audio files in the file drop, as one playlist. The browser plays them from
// the box; no request leaves it.
(() => {
  const list = document.getElementById("music-list");
  const player = document.getElementById("music-player");
  const now = document.getElementById("music-now");
  const nextButton = document.getElementById("music-next");
  const prevButton = document.getElementById("music-prev");
  let tracks = [];
  let at = -1;

  const AUDIO = /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|webm)$/i;
  const size = (n) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);
  const url = (file) => `/api/drop/${encodeURIComponent(file.id)}`;

  function play(index) {
    if (!tracks.length) return;
    at = (index + tracks.length) % tracks.length;
    const file = tracks[at];
    player.src = url(file);
    player.play().catch(() => {});
    now.textContent = `${file.name} (${size(file.size)})`;
    [...list.querySelectorAll("button")].forEach((b, i) => b.classList.toggle("selected", i === at));
  }

  player.addEventListener("ended", () => play(at + 1));
  nextButton.addEventListener("click", () => play(at + 1));
  prevButton.addEventListener("click", () => play(at - 1));

  async function refresh() {
    try {
      const response = await fetch("/api/drop");
      const data = await response.json();
      tracks = (data.files || []).filter((file) => AUDIO.test(file.name));
      list.replaceChildren(
        ...(tracks.length
          ? tracks.map((file, index) => {
              const item = document.createElement("li");
              const button = document.createElement("button");
              button.type = "button";
              button.className = "action-btn";
              button.textContent = `${file.name} (${size(file.size)})`;
              button.addEventListener("click", () => play(index));
              item.append(button);
              return item;
            })
          : [
              Object.assign(document.createElement("li"), {
                className: "setting-desc",
                textContent: "No audio files are in the file drop yet. Upload some on the File drop.",
              }),
            ]),
      );
      if (at >= tracks.length) at = -1;
    } catch (_) {
      list.replaceChildren(Object.assign(document.createElement("li"), { className: "setting-desc", textContent: "Could not reach the box." }));
    }
  }

  refresh();
  setInterval(refresh, 15000);
})();
