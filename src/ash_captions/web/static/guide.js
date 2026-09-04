/* The editor's guide page: the setup checklist (remembered per browser),
   a Copy button on every command block, and the section highlight in the
   side nav. Loaded by guide.html; inlined into docs/ASH-Captions-Guide.html
   by scripts/export_guide.py so the checklist works from a file too. */
(function () {
  var KEY = "ashguide.done.v1";
  var boxes = Array.prototype.slice.call(document.querySelectorAll('.step input[type="checkbox"]'));
  var label = document.getElementById("progress-label");
  var bar = document.getElementById("progress-bar");
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; } }
  function save(state) { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
  function render() {
    var done = 0;
    boxes.forEach(function (b) { b.closest(".step").classList.toggle("done", b.checked); if (b.checked) done++; });
    label.textContent = "Setup: " + done + " of " + boxes.length + " steps done";
    bar.style.width = (100 * done / boxes.length) + "%";
  }
  var state = load();
  boxes.forEach(function (b) {
    b.checked = !!state[b.id];
    b.addEventListener("change", function () { state[b.id] = b.checked; save(state); render(); });
  });
  document.getElementById("progress-reset").addEventListener("click", function () {
    state = {}; save(state); boxes.forEach(function (b) { b.checked = false; }); render();
  });
  render();

  // Copy buttons on every command block.
  Array.prototype.forEach.call(document.querySelectorAll(".cmd"), function (block) {
    var pre = block.querySelector("pre");
    var btn = document.createElement("button");
    btn.type = "button"; btn.className = "btn small"; btn.textContent = "Copy";
    btn.addEventListener("click", function () {
      var text = pre.textContent;
      var done = function () { btn.textContent = "Copied"; btn.classList.add("copied"); setTimeout(function () { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, done); }
      else { var r = document.createRange(); r.selectNodeContents(pre); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); try { document.execCommand("copy"); } catch (e) {} s.removeAllRanges(); done(); }
    });
    block.appendChild(btn);
  });

  // Highlight the section in view in the side nav.
  var links = Array.prototype.slice.call(document.querySelectorAll(".guide-nav a"));
  var sections = links.map(function (a) { return document.querySelector(a.getAttribute("href")); });
  function highlight() {
    var y = window.scrollY + 80, current = 0;
    sections.forEach(function (s, i) { if (s && s.offsetTop <= y) current = i; });
    links.forEach(function (a, i) { a.classList.toggle("active", i === current); });
  }
  window.addEventListener("scroll", highlight, { passive: true });
  highlight();
})();
