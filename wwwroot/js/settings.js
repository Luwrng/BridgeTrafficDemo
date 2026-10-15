(() => {
  const dialog = document.getElementById("cameraSettingsDialog");
  const openButton = document.getElementById("openSettingsBtn");
  const tabs = [...dialog.querySelectorAll(".settings-camera-tab")];
  let selectedCamera = "A";

  function maskLink(cameraId) {
    document.getElementById(`camera${cameraId}Url`).type = "password";
    const reveal = document.getElementById(`camera${cameraId}ShowUrlBtn`);
    reveal.textContent = "Hiện"; reveal.setAttribute("aria-pressed", "false");
  }

  function selectCamera(cameraId, focus = false) {
    selectedCamera = cameraId;
    for (const tab of tabs) {
      const selected = tab.dataset.camera === cameraId;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      const pane = document.getElementById(tab.getAttribute("aria-controls"));
      pane.hidden = !selected;
      pane.querySelector(".camera-settings").open = selected;
      if (!selected) maskLink(tab.dataset.camera);
      if (selected && focus) tab.focus();
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => selectCamera(tab.dataset.camera));
    tab.addEventListener("keydown", event => {
      const index = tabs.indexOf(tab);
      const next = { ArrowDown: (index + 1) % tabs.length, ArrowRight: (index + 1) % tabs.length,
        ArrowUp: (index + tabs.length - 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
        Home: 0, End: tabs.length - 1 }[event.key];
      if (next !== undefined) {
        event.preventDefault(); selectCamera(tabs[next].dataset.camera, true);
      }
    });
  }
  openButton.addEventListener("click", () => {
    window.FunctionDrawer.open('cameraSettingsDialog');
    if (!dialog.open) dialog.show();
    selectCamera(selectedCamera, true);
    window.dispatchEvent(new Event("camera-settings-open"));
  });
  document.getElementById("closeSettingsBtn").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    for (const tab of tabs) {
      maskLink(tab.dataset.camera);
      document.getElementById(tab.getAttribute("aria-controls")).querySelector(".camera-settings").open = false;
    }
  });
  selectCamera(selectedCamera);
})();
