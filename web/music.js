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
  let playing = null; // the track's id, not its place in the list: a refresh must not move it

  const AUDIO = /\.(mp3|m4a|aac|ogg|oga|opus|wav|flac|webm)$/i;
  const size = (n) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`);
  const url = (file) => `/api/drop/${encodeURIComponent(file.id)}`;

  function mark() {
    [...list.querySelectorAll("button")].forEach((b) => b.classList.toggle("selected", b.dataset.id === playing));
  }

  function play(index) {
    if (!tracks.length) return;
    const at = ((index % tracks.length) + tracks.length) % tracks.length;
    const file = tracks[at];
    playing = file.id;
    player.src = url(file);
    player.play().catch(() => {});
    now.textContent = `${file.name} (${size(file.size)})`;
    mark();
  }

  player.addEventListener("ended", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    play(at + 1);
  });
  nextButton.addEventListener("click", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    play(at + 1);
  });
  prevButton.addEventListener("click", () => {
    const at = tracks.findIndex((file) => file.id === playing);
    play(at - 1);
  });

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
              button.dataset.id = file.id;
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
      if (!tracks.some((file) => file.id === playing)) playing = null;
      mark();
    } catch (_) {
      list.replaceChildren(Object.assign(document.createElement("li"), { className: "setting-desc", textContent: "Could not reach the box." }));
    }
  }

  refresh();
  setInterval(refresh, 15000);
})();
