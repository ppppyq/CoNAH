import os
import sys

sys.path.insert(0, os.path.abspath("../../"))

project = "CoNAH"
author = "CoNAH Contributors"
copyright = "2026, CoNAH Contributors"
version = "latest"
release = version

extensions = [
    "sphinx_copybutton",
    "sphinx_design",
    "sphinx_sitemap",
]

source_suffix = {".rst": "restructuredtext"}
root_doc = "index"
templates_path = ["_templates"]
exclude_patterns = ["**/_*.rst"]
default_role = "code"

language = "zh"
html_search_language = "zh"
html_theme = "pydata_sphinx_theme"
html_title = "CoNAH 中文文档"
html_show_sourcelink = False
html_baseurl = os.environ.get(
    "READTHEDOCS_CANONICAL_URL",
    "https://conah.readthedocs.io/zh-cn/latest/",
)
sitemap_url_scheme = "{link}"
html_static_path = ["../_static"]
html_css_files = ["css/custom.css"]
html_js_files = [
    "js/lang-switcher.js",
    "js/sidebar-nav.js",
    "js/theme-toggle.js",
]

html_sidebars = {
    "**": [
        "sidebar-brand",
        "search-field",
        "sidebar-tools",
        "global-sidebar-nav",
    ]
}

html_theme_options = {
    "search_bar_text": "搜索文档…",
    "navbar_start": [],
    "navbar_center": [],
    "navbar_end": [],
    "navbar_align": "left",
    "secondary_sidebar_items": {"**": ["page-toc"], "index": []},
    "collapse_navigation": False,
    "show_nav_level": 1,
    "navigation_depth": 4,
    "icon_links": [
        {
            "name": "GitHub",
            "url": "https://github.com/ppppyq/CoNAH",
            "icon": "fab fa-github",
            "type": "fontawesome",
        }
    ],
}
