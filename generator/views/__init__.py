"""One module per page family. A view prepares data from the model (sorting,
filtering, grouping) and renders it through `render.tpl()` from a Jinja2
template under generator/templates/; it carries no markup of its own. Views
render their pages ON IMPORT; build.py imports them in build order. Views
read the model and call render helpers; they never derive facts of their own."""
