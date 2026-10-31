function initNotesFormatting() {
  var textarea = document.getElementById('s-notes');
  var toolbar = document.getElementById('notes-formatting');
  if (!textarea || !toolbar || toolbar.dataset.armed) return;
  toolbar.dataset.armed = '1';

  var linkDialog = document.getElementById('nf-link-dialog');
  var linkText = document.getElementById('nf-link-text');
  var linkUrl = document.getElementById('nf-link-url');
  var linkError = document.getElementById('nf-link-error');
  var tableDialog = document.getElementById('nf-table-dialog');
  var tableRows = document.getElementById('nf-table-rows');
  var tableColumns = document.getElementById('nf-table-columns');
  var tableError = document.getElementById('nf-table-error');
  var headingLevel = document.getElementById('nf-heading-level');
  var pendingLink = null;

  function selection() {
    return {
      start: textarea.selectionStart,
      end: textarea.selectionEnd,
    };
  }

  function replaceRange(text, start, end, selectStart, selectEnd) {
    textarea.focus();
    textarea.setSelectionRange(start, end);
    if (
      typeof document.execCommand === 'function' &&
      document.execCommand('insertText', false, text)
    ) {
      textarea.setSelectionRange(selectStart, selectEnd);
      return;
    }
    var value = textarea.value;
    textarea.value = value.slice(0, start) + text + value.slice(end);
    textarea.setSelectionRange(selectStart, selectEnd);
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function wrapSelection(marker) {
    var range = selection();
    var selected = textarea.value.slice(range.start, range.end);
    var before = textarea.value.slice(range.start - marker.length, range.start);
    var after = textarea.value.slice(range.end, range.end + marker.length);
    if (selected && before === marker && after === marker) {
      replaceRange(
        selected,
        range.start - marker.length,
        range.end + marker.length,
        range.start - marker.length,
        range.end - marker.length
      );
      return;
    }
    if (!selected) {
      replaceRange(
        marker + marker,
        range.start,
        range.end,
        range.start + marker.length,
        range.start + marker.length
      );
      return;
    }
    replaceRange(
      marker + selected + marker,
      range.start,
      range.end,
      range.start + marker.length,
      range.end + marker.length
    );
  }

  function prefixLines(prefix, alreadyPrefixed, removePrefix) {
    var value = textarea.value;
    var range = selection();
    var start = value.lastIndexOf('\n', range.start - 1) + 1;
    var end = value.indexOf('\n', range.end);
    if (end < 0) end = value.length;
    var lines = value.slice(start, end).split('\n');
    var unprefix = lines.every(function (line, index) {
      return alreadyPrefixed(line, index);
    });
    var changed = lines
      .map(function (line, index) {
        return unprefix ? line.replace(removePrefix, '') : prefix(index) + line;
      })
      .join('\n');
    replaceRange(changed, start, end, start, start + changed.length);
  }

  function applyLineAction(action) {
    if (action === 'heading') {
      var level = Number(headingLevel.value);
      if (!Number.isInteger(level) || level < 1 || level > 6) return;
      var marker = '#'.repeat(level) + ' ';
      var value = textarea.value;
      var range = selection();
      var start = value.lastIndexOf('\n', range.start - 1) + 1;
      var end = value.indexOf('\n', range.end);
      if (end < 0) end = value.length;
      var lines = value.slice(start, end).split('\n');
      var removeHeading = lines.every(function (line) {
        return line.startsWith(marker);
      });
      var changed = lines
        .map(function (line) {
          if (removeHeading) return line.replace(/^#{1,6} /, '');
          return marker + line.replace(/^#{1,6} /, '');
        })
        .join('\n');
      replaceRange(changed, start, end, start, start + changed.length);
    } else if (action === 'quote') {
      prefixLines(
        function () {
          return '> ';
        },
        function (line) {
          return line.startsWith('> ');
        },
        /^> /
      );
    } else if (action === 'bullets') {
      prefixLines(
        function () {
          return '- ';
        },
        function (line) {
          return line.startsWith('- ');
        },
        /^- /
      );
    } else if (action === 'numbers') {
      prefixLines(
        function (index) {
          return String(index + 1) + '. ';
        },
        function (line) {
          return /^\d+\. /.test(line);
        },
        /^\d+\. /
      );
    }
  }

  function linkAtCaret() {
    var value = textarea.value;
    var range = selection();
    var selected = value.slice(range.start, range.end);
    var linked = /^\[([^\]]*)\]\(([^)\s]+)\)$/.exec(selected);
    if (linked) {
      return {
        start: range.start,
        end: range.end,
        label: linked[1],
        url: linked[2],
      };
    }
    var links = /\[([^\]]*)\]\(([^)\s]+)\)/g;
    var match;
    while ((match = links.exec(value))) {
      if (
        range.start >= match.index &&
        range.start <= match.index + match[0].length
      ) {
        return {
          start: match.index,
          end: match.index + match[0].length,
          label: match[1],
          url: match[2],
        };
      }
    }
    var urls = /https?:\/\/[^\s<>]+/gi;
    while ((match = urls.exec(value))) {
      if (
        range.start >= match.index &&
        range.start <= match.index + match[0].length
      ) {
        return {
          start: match.index,
          end: match.index + match[0].length,
          label: match[0],
          url: match[0],
        };
      }
    }
    return { start: range.start, end: range.end, label: selected, url: '' };
  }

  function showLinkDialog(details) {
    pendingLink = { start: details.start, end: details.end };
    linkText.value = details.label;
    linkUrl.value = details.url;
    linkError.hidden = true;
    linkDialog.hidden = false;
    linkText.focus();
    linkText.select();
  }

  function closeLinkDialog() {
    linkDialog.hidden = true;
    pendingLink = null;
    textarea.focus();
  }

  function confirmLink() {
    var label = linkText.value.trim();
    var url = linkUrl.value.trim();
    if (!label || !url) {
      linkError.hidden = false;
      return;
    }
    var escapedLabel = label.replace(/\\/g, '\\\\').replace(/\]/g, '\\]');
    var markdown = '[' + escapedLabel + '](' + url + ')';
    replaceRange(
      markdown,
      pendingLink.start,
      pendingLink.end,
      pendingLink.start + markdown.length,
      pendingLink.start + markdown.length
    );
    closeLinkDialog();
  }

  function showTableDialog() {
    tableError.hidden = true;
    tableRows.value = '3';
    tableColumns.value = '3';
    tableDialog.hidden = false;
    tableRows.focus();
  }

  function confirmTable() {
    var rows = Number(tableRows.value);
    var columns = Number(tableColumns.value);
    if (
      !Number.isSafeInteger(rows) ||
      rows < 1 ||
      !Number.isInteger(columns) ||
      columns < 1 ||
      columns > 9
    ) {
      tableError.hidden = false;
      return;
    }
    var headings = [];
    var separators = [];
    var cells = [];
    for (var column = 1; column <= columns; column += 1) {
      headings.push('Header ' + column);
      separators.push('---');
      cells.push('Cell');
    }
    var lines = [
      '| ' + headings.join(' | ') + ' |',
      '| ' + separators.join(' | ') + ' |',
    ];
    for (var row = 1; row < rows; row += 1) {
      lines.push('| ' + cells.join(' | ') + ' |');
    }
    var range = selection();
    var before = textarea.value.slice(0, range.start);
    var after = textarea.value.slice(range.end);
    var leading =
      before && !before.endsWith('\n\n')
        ? before.endsWith('\n')
          ? '\n'
          : '\n\n'
        : '';
    var trailing =
      after && !after.startsWith('\n\n')
        ? after.startsWith('\n')
          ? '\n'
          : '\n\n'
        : '';
    var table = leading + lines.join('\n') + trailing;
    replaceRange(
      table,
      range.start,
      range.end,
      range.start + table.length,
      range.start + table.length
    );
    tableDialog.hidden = true;
  }

  var actions = {
    bold: function () {
      wrapSelection('**');
    },
    italic: function () {
      wrapSelection('*');
    },
    strike: function () {
      wrapSelection('~~');
    },
    code: function () {
      wrapSelection('`');
    },
    heading: function () {
      applyLineAction('heading');
    },
    bullets: function () {
      applyLineAction('bullets');
    },
    numbers: function () {
      applyLineAction('numbers');
    },
    quote: function () {
      applyLineAction('quote');
    },
    link: function () {
      showLinkDialog(linkAtCaret());
    },
    table: showTableDialog,
  };
  Object.keys(actions).forEach(function (action) {
    var button = document.getElementById('nf-' + action);
    button.addEventListener('mousedown', function (event) {
      event.preventDefault();
    });
    button.addEventListener('click', actions[action]);
  });

  document.getElementById('nf-link-ok').addEventListener('click', confirmLink);
  document
    .getElementById('nf-link-cancel')
    .addEventListener('click', closeLinkDialog);
  [linkText, linkUrl].forEach(function (field) {
    field.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        confirmLink();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeLinkDialog();
      }
    });
  });
  document
    .getElementById('nf-table-ok')
    .addEventListener('click', confirmTable);
  document
    .getElementById('nf-table-cancel')
    .addEventListener('click', function () {
      tableDialog.hidden = true;
      textarea.focus();
    });
  [tableRows, tableColumns].forEach(function (field) {
    field.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        confirmTable();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        tableDialog.hidden = true;
        textarea.focus();
      }
    });
  });

  textarea.addEventListener('paste', function (event) {
    var range = selection();
    if (range.start === range.end || !event.clipboardData) return;
    var pasted = event.clipboardData.getData('text/plain');
    var url = /^(https?:\/\/\S+)/i.exec(pasted);
    if (!url) return;
    event.preventDefault();
    showLinkDialog({
      start: range.start,
      end: range.end,
      label: textarea.value.slice(range.start, range.end),
      url: url[1],
    });
  });

  textarea.addEventListener('keydown', function (event) {
    if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
    var key = event.key.toLowerCase();
    if (key === 'b' || key === 'i') {
      event.preventDefault();
      wrapSelection(key === 'b' ? '**' : '*');
    } else if (key === 'k') {
      event.preventDefault();
      showLinkDialog(linkAtCaret());
    }
  });
}

export { initNotesFormatting };
