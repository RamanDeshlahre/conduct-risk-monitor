// Clientside callbacks: these run in the browser, so filtering and copying feel instant.
window.dash_clientside = Object.assign({}, window.dash_clientside, {
  crm: {
    // Show only the RAG statuses the user has ticked. Rows come from a Store holding the full watchlist.
    filterWatchlist: function (selected, rows) {
      if (!Array.isArray(rows)) {
        return [];
      }
      var keep = new Set(selected || []);
      return rows.filter(function (row) {
        return keep.has(row.rag);
      });
    },

    // Copy the plain-English summary so it can be pasted straight into an email or MI pack.
    copySummary: function (nClicks, text) {
      if (!nClicks) {
        return window.dash_clientside.no_update;
      }
      if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text || "").then(
          function () { return "Summary copied"; },
          function () { return "Copy was blocked by the browser. Select the headline and copy it instead."; }
        );
      }
      return "Copy needs a secure (https) page. Select the headline and copy it instead.";
    }
  }
});
