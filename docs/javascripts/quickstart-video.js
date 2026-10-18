(() => {
  function preparePlayers() {
    document.querySelectorAll(".quickstart-player").forEach((player) => {
      if (player.dataset.ready) return;
      const video = player.querySelector("video");
      const button = player.querySelector(".quickstart-play");
      if (!video || !button) return;
      player.dataset.ready = "true";
      const label = button.innerHTML;
      button.addEventListener("click", async () => {
        try {
          await video.play();
          video.focus();
        } catch (_) {
          button.hidden = false;
          button.innerHTML = label;
        }
      });
      video.addEventListener("playing", () => { button.hidden = true; });
      video.addEventListener("ended", () => { button.hidden = false; });
      video.addEventListener("error", () => { button.hidden = true; });
      button.hidden = false;
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", preparePlayers);
  } else {
    preparePlayers();
  }
  if (typeof document$ !== "undefined") document$.subscribe(preparePlayers);
})();
