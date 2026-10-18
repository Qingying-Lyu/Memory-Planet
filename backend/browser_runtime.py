"""One packaged Chromium for all headless readers."""
import os
from .store import ROOT
def launch(playwright):
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'browsers')
    executables=list((ROOT/'browsers').glob('chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe'))
    if len(executables)!=1:raise ValueError('包内Chromium不完整，请重新完整解压')
    return playwright.chromium.launch(headless=True,executable_path=str(executables[0]))
