"""Visit the Streamlit app in a real browser; if it is asleep, click the wake-up button.

Streamlit Community Cloud puts apps to sleep after 12 hours without traffic, and only a
browser click on "Yes, get this app back up!" wakes them, so a plain HTTP ping is not enough.
"""
import os
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = os.environ["STREAMLIT_APP_URL"]
WAKE_BUTTON = (By.XPATH, "//button[contains(., 'get this app back up')]")

options = Options()
for arg in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
    options.add_argument(arg)

driver = webdriver.Chrome(options=options)
try:
    driver.get(URL)
    try:
        WebDriverWait(driver, 20).until(EC.element_to_be_clickable(WAKE_BUTTON)).click()
        print("App was asleep: clicked the wake-up button, waiting for it to boot...")
        WebDriverWait(driver, 180).until(EC.invisibility_of_element_located(WAKE_BUTTON))
    except TimeoutException:
        # Either no wake button appeared (app already awake) or it never finished booting.
        if driver.find_elements(*WAKE_BUTTON):
            raise SystemExit("Clicked wake-up but the app did not come back within 180s")
        print("No wake-up button: app is already awake.")
    # Stay on the page so the app registers as a live visit.
    time.sleep(30)
    print("Done.")
finally:
    driver.quit()
