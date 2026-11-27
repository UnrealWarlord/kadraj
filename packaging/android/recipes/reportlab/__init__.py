"""KADRAJ yerel tarifi: reportlab (saf Python).

reportlab 4 ve sonrası saf Python'dur (C hızlandırıcıları ayrı ``rl_accel`` / ``rl_renderPM`` paketlerindedir).
python-for-android'in kendi ``reportlab`` tarifi eski bir sürümü hg.reportlab.com'dan C eklentileriyle derliyor; bu
yerel tarif (``p4a.local_recipes``) onun yerine PyPI'daki sürümü kurar. KADRAJ'da ``.pdf`` senaryo dışa aktarımı.
"""
from pythonforandroid.recipe import PyProjectRecipe


class ReportLabPureRecipe(PyProjectRecipe):
    name = "reportlab"
    version = "5.1.2"
    url = "https://pypi.python.org/packages/source/r/reportlab/reportlab-{version}.tar.gz"
    depends = ["Pillow", "charset-normalizer"]


recipe = ReportLabPureRecipe()
