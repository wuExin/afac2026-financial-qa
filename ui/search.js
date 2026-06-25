/* afac-search-engine: in-document find (Ctrl+F) shared across md/pdf/compare templates */
(function () {
  "use strict";

  // ---- styles -------------------------------------------------------------
  var style = document.createElement("style");
  style.textContent =
    "#search-bar{flex:none;display:flex;gap:6px;align-items:center;" +
    "background:#fff;border-bottom:1px solid #ddd;padding:6px 8px;" +
    "font:13px/1.4 sans-serif;}" +
    "#search-bar input{flex:0 1 240px;padding:3px 6px;font:inherit;}" +
    "#search-bar button{cursor:pointer;border:1px solid #ccc;background:#f7f7f7;" +
    "border-radius:4px;padding:2px 8px;font:inherit;}" +
    "#search-count{color:#555;min-width:120px;}" +
    "mark.hit{background:rgba(255,230,0,.45);color:inherit;padding:0;}" +
    "mark.hit.active{background:rgba(255,145,0,.8);}";
  document.head.appendChild(style);

  // ---- toolbar ------------------------------------------------------------
  var bar = document.createElement("div");
  bar.id = "search-bar";
  bar.innerHTML =
    '<input id="search-input" type="text" placeholder="查找文档内容…" />' +
    '<span id="search-count"></span>' +
    '<button id="search-prev" title="上一个 (Shift+Enter)">↑</button>' +
    '<button id="search-next" title="下一个 (Enter)">↓</button>' +
    '<button id="search-clear" title="清除 (Esc)">✕</button>';
  document.body.insertBefore(bar, document.body.firstChild);

  var input = bar.querySelector("#search-input");
  var countEl = bar.querySelector("#search-count");
  var roots = Array.prototype.slice.call(
    document.querySelectorAll("[data-search-root]")
  );

  var matches = []; // { markEl, rootIndex }
  var current = -1;
  var query = "";

  // ---- clear previous marks ----------------------------------------------
  function clearMarks() {
    for (var r = 0; r < roots.length; r++) {
      var marks = roots[r].querySelectorAll("mark.hit");
      for (var i = 0; i < marks.length; i++) {
        var m = marks[i];
        var parent = m.parentNode;
        while (m.firstChild) parent.insertBefore(m.firstChild, m);
        parent.removeChild(m);
        parent.normalize();
      }
    }
    matches = [];
    current = -1;
  }

  // ---- collect text nodes under a root -----------------------------------
  function textNodesIn(root) {
    var walker = document.createTreeWalker(
      root,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: function (node) {
          if (!node.nodeValue || !node.nodeValue.trim())
            return NodeFilter.FILTER_REJECT;
          var p = node.parentNode;
          if (p && (p.nodeName === "SCRIPT" || p.nodeName === "STYLE"))
            return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      }
    );
    var nodes = [];
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    return nodes;
  }

  // ---- wrap occurrences of query inside one text node --------------------
  function wrapInNode(node, rootIndex, q) {
    var text = node.nodeValue;
    var hay = text.toLowerCase();
    var idx = hay.indexOf(q);
    if (idx === -1) return;
    var frag = document.createDocumentFragment();
    var pos = 0;
    while (idx !== -1) {
      if (idx > pos)
        frag.appendChild(document.createTextNode(text.slice(pos, idx)));
      var mark = document.createElement("mark");
      mark.className = "hit";
      mark.textContent = text.slice(idx, idx + q.length);
      frag.appendChild(mark);
      matches.push({ markEl: mark, rootIndex: rootIndex });
      pos = idx + q.length;
      idx = hay.indexOf(q, pos);
    }
    if (pos < text.length)
      frag.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(frag, node);
  }

  // ---- run a search -------------------------------------------------------
  function runSearch() {
    clearMarks();
    var q = query.toLowerCase();
    if (q) {
      for (var r = 0; r < roots.length; r++) {
        var nodes = textNodesIn(roots[r]);
        for (var i = 0; i < nodes.length; i++) wrapInNode(nodes[i], r, q);
      }
      if (matches.length) activate(0, false);
    }
    updateCount();
  }

  function activate(i, scroll) {
    if (!matches.length) return;
    if (current >= 0 && matches[current])
      matches[current].markEl.classList.remove("active");
    current = (i + matches.length) % matches.length;
    var m = matches[current].markEl;
    m.classList.add("active");
    if (scroll !== false)
      m.scrollIntoView({ block: "center", inline: "nearest" });
    updateCount();
  }

  function updateCount() {
    if (!query) {
      countEl.textContent = "";
      return;
    }
    if (!matches.length) {
      countEl.textContent = "无结果";
      return;
    }
    var pos = (current + 1) + "/" + matches.length;
    if (roots.length > 1) {
      var per = [];
      for (var r = 0; r < roots.length; r++) {
        var label = roots[r].getAttribute("data-search-label") || ("区" + (r + 1));
        var c = 0;
        for (var i = 0; i < matches.length; i++)
          if (matches[i].rootIndex === r) c++;
        per.push(label + " " + c);
      }
      countEl.textContent = per.join(" / ") + "  (" + pos + ")";
    } else {
      countEl.textContent = pos;
    }
  }

  // ---- events -------------------------------------------------------------
  var debounceTimer = null;
  input.addEventListener("input", function () {
    query = input.value;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSearch, 120);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) activate(current - 1, true);
      else activate(current + 1, true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      input.value = "";
      query = "";
      clearMarks();
      updateCount();
    }
  });
  bar.querySelector("#search-next").addEventListener("click", function () {
    activate(current + 1, true);
  });
  bar.querySelector("#search-prev").addEventListener("click", function () {
    activate(current - 1, true);
  });
  bar.querySelector("#search-clear").addEventListener("click", function () {
    input.value = "";
    query = "";
    clearMarks();
    updateCount();
  });

  // PDF text layers render asynchronously; re-index when they signal ready.
  document.addEventListener("searchcontentready", function () {
    if (query) runSearch();
  });
})();
