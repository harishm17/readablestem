-- The text changes the Word export makes to what pandoc read from the page
-- (pipeline/export_docx.py passes this with --lua-filter). It runs inside pandoc, on the
-- parsed document, and sees only what pandoc kept: it can name nothing outside the page.
--
--   * a page marker ("Page 12", `div.pagebreak`) goes: a Word document has its own pages;
--   * the skip link, which only a web page needs, goes;
--   * a figure with no picture in it (a picture left out, said in words) is written as
--     paragraphs: pandoc writes a figure as a one-cell table, a layout table for a screen
--     reader. Its words stay, the caption last;
--   * an equation pandoc cannot write as a Word equation (a MathML shape its TeX step
--     does not accept leaves the TeX in the text, or nothing at all) is written as the
--     words below in the "Not Converted" style, so that one equation does not fail the file
--     and the file never holds raw TeX. The page wraps each `<math>` in
--     `<span data-eq="KEY" data-reading="...">`. Every one is tried once, in a single
--     test document, with the writer that makes the file; the keys that fail are written to
--     stderr as `RSFAIL KEY` for the export to count (pipeline/export_docx.py).

function Div(div)
  if div.classes:includes("pagebreak") then
    return {}
  end
end

function Link(link)
  if link.classes:includes("skip-link") then
    return {}
  end
end

function Figure(figure)
  local has_picture = false
  figure:walk({
    Image = function()
      has_picture = true
    end,
  })
  if has_picture then
    return nil
  end
  local blocks = pandoc.List(figure.content)
  for _, block in ipairs(figure.caption.long) do
    if block.t == "Plain" then
      block = pandoc.Para(block.content)
    end
    blocks:insert(block)
  end
  return blocks
end

local NOT_WORD = "This equation could not be written as a Word equation; it is complete on the web page."

-- The equations of the page, tried together: one paragraph each, written as a Word file in
-- memory, so a failure is found with the writer that will make the real file and costs one
-- run. An empty `<math>` leaves the page's span with no equation in it: that is a failure too.
local function failed_equations(doc)
  local entries, test = {}, {}
  doc:walk({
    Span = function(span)
      local key = span.attributes["eq"]
      if key then
        local math = nil
        span:walk({ Math = function(m) math = math or m end })
        entries[#entries + 1] = { key = key, math = math }
        if math then
          test[#test + 1] = pandoc.Para({ pandoc.Math("InlineMath", math.text) })
        end
      end
    end,
  })
  local failed = {}
  local xml = nil
  if #test > 0 then
    local archive = pandoc.zip.Archive(pandoc.write(pandoc.Pandoc(test), "docx"))
    for _, entry in ipairs(archive.entries) do
      if entry.path == "word/document.xml" then
        xml = entry:contents()
      end
    end
  end
  local n = 0
  local ok = {}
  for paragraph in (xml or ""):gmatch("<w:p[ >].-</w:p>") do
    n = n + 1
    ok[n] = paragraph:find("<m:oMath", 1, true) ~= nil
  end
  if n ~= #test then
    return failed, entries -- the test file is not what was written: say nothing, the export checks
  end
  local at = 0
  for _, entry in ipairs(entries) do
    if entry.math then
      at = at + 1
    end
    if not entry.math or not ok[at] then
      failed[entry.key] = true
    end
  end
  return failed, entries
end

function Pandoc(doc)
  local failed, entries = failed_equations(doc)
  if #entries == 0 then
    return nil
  end
  for key in pairs(failed) do
    io.stderr:write("RSFAIL ", key, "\n")
  end
  return doc:walk({
    Span = function(span)
      local key = span.attributes["eq"]
      if not key then
        return nil
      end
      if not failed[key] then
        return span.content
      end
      local reading = span.attributes["reading"] or ""
      local text = reading ~= "" and (reading .. ". " .. NOT_WORD) or NOT_WORD
      return pandoc.Span({ pandoc.Str(text) }, pandoc.Attr("", {}, { { "custom-style", "Not Converted" } }))
    end,
  })
end
