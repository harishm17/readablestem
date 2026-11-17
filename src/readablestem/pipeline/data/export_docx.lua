-- The text changes the Word export makes to what pandoc read from the page
-- (pipeline/export_docx.py passes this with --lua-filter). It runs inside pandoc, on the
-- parsed document, and sees only what pandoc kept: it can name nothing outside the page.
--
--   * a page marker ("Page 12", `div.pagebreak`) goes: a Word document has its own pages;
--   * the skip link, which only a web page needs, goes;
--   * a figure with no picture in it (a picture left out, said in words) is written as
--     paragraphs: pandoc writes a figure as a one-cell table, a layout table for a screen
--     reader. Its words stay, the caption last.

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
