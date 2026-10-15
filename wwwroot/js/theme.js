(() => {
  // Chỉ lưu trắng/đen trên trình duyệt, không lưu RTSP hay dữ liệu xe.
  const key = "bridgeTrafficAppearance";
  const button = document.getElementById("openThemeBtn");
  let theme = "light";
  try { if (JSON.parse(localStorage.getItem(key))?.theme === "dark") theme = "dark"; } catch {}
  function apply() {
    document.documentElement.dataset.theme = theme;
    const label = theme === "light" ? "Chuyển sang nền đen" : "Chuyển sang nền trắng";
    button.textContent = theme === "light" ? "☾" : "☀";
    button.setAttribute("aria-label", label); button.title = label;
  }
  button.addEventListener("click", () => {
    theme = theme === "light" ? "dark" : "light";
    apply();
    try { localStorage.setItem(key, JSON.stringify({ theme })); } catch {}
  });
  apply();
})();
