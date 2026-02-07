import os
import csv
import io
import time
from flask import Flask, render_template, request, Response, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

app = Flask(__name__)

def get_chrome_driver():
    """Configure and return a Chrome WebDriver for headless operation."""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=nl")
    options.add_argument("--accept-lang=nl-NL,nl")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Check for Railway/Docker environment
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    # Use ChromeDriverManager for automatic driver management
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def scrape_people_also_ask(keyword):
    """
    Scrape the 'People Also Ask' section from Google.nl for a given keyword.
    Returns a list of dictionaries with question, answer, and source information.
    """
    driver = None
    results = []

    try:
        driver = get_chrome_driver()

        # Navigate to Google Netherlands with Dutch language settings
        # Using gl=nl (country) and hl=nl (language) parameters
        search_url = f"https://www.google.nl/search?q={keyword}&hl=nl&gl=nl"
        driver.get(search_url)

        # Wait for page to load
        time.sleep(2)

        # Handle cookie consent dialog (GDPR)
        try:
            # Look for the cookie consent button
            cookie_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Alles accepteren') or contains(., 'Accepteren') or contains(., 'Accept')]")
            if cookie_buttons:
                cookie_buttons[0].click()
                time.sleep(1)
        except Exception:
            pass  # No cookie dialog or already accepted

        # Wait for and find the "People also ask" section (in Dutch: "Anderen zochten ook naar" or "Gerelateerde vragen")
        try:
            # Try multiple possible selectors for PAA section
            paa_selectors = [
                "//div[@jscontroller and @jsname and @jsaction][.//div[@role='heading']]",
                "//div[contains(@class, 'related-question')]",
                "//div[@data-sgrd='true']",
            ]

            people_also_ask_div = None
            for selector in paa_selectors:
                try:
                    people_also_ask_div = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    if people_also_ask_div:
                        break
                except TimeoutException:
                    continue

            if not people_also_ask_div:
                # Try finding by data-sgrd attribute which is commonly used
                people_also_ask_div = driver.find_element(By.CSS_SELECTOR, "[data-sgrd='true']")

            # Find all question elements
            question_elements = driver.find_elements(By.CSS_SELECTOR, "[data-sgrd='true'] > div[jsname]")

            if not question_elements:
                # Alternative: find expandable question containers
                question_elements = driver.find_elements(By.XPATH, "//div[@jscontroller][@jsaction][contains(@jsaction, 'click')]")

            for element in question_elements:
                try:
                    # Check if this looks like a PAA question
                    jsname = element.get_attribute("jsname")
                    if not jsname:
                        continue

                    # Click to expand the question
                    element.click()
                    time.sleep(0.5)

                    # Extract question title
                    question_title = ""
                    try:
                        title_elem = element.find_element(By.CSS_SELECTOR, "[aria-expanded='true'] span")
                        question_title = title_elem.text
                    except NoSuchElementException:
                        try:
                            title_elem = element.find_element(By.CSS_SELECTOR, "[role='button'] span")
                            question_title = title_elem.text
                        except NoSuchElementException:
                            continue

                    if not question_title:
                        continue

                    # Extract answer/description
                    question_description = ""
                    try:
                        desc_elem = element.find_element(By.CSS_SELECTOR, "[data-attrid='wa:/description'] span[lang]")
                        question_description = desc_elem.text
                    except NoSuchElementException:
                        try:
                            desc_elem = element.find_element(By.CSS_SELECTOR, "div[data-md] span")
                            question_description = desc_elem.text
                        except NoSuchElementException:
                            pass

                    # Extract source URL
                    source_url = ""
                    try:
                        source_elem = element.find_element(By.XPATH, ".//h3/ancestor::a")
                        source_url = source_elem.get_attribute("href")
                    except NoSuchElementException:
                        try:
                            source_elem = element.find_element(By.CSS_SELECTOR, "a[href]")
                            source_url = source_elem.get_attribute("href")
                        except NoSuchElementException:
                            pass

                    # Extract source title
                    source_title = ""
                    try:
                        h3_elem = element.find_element(By.CSS_SELECTOR, "h3")
                        source_title = h3_elem.text
                    except NoSuchElementException:
                        pass

                    if question_title:  # Only add if we have a valid question
                        results.append({
                            "question": question_title,
                            "answer": question_description,
                            "source_title": source_title,
                            "source_url": source_url
                        })

                except Exception as e:
                    continue  # Skip problematic elements

        except TimeoutException:
            pass  # No PAA section found
        except NoSuchElementException:
            pass

    except Exception as e:
        print(f"Error during scraping: {e}")

    finally:
        if driver:
            driver.quit()

    return results


@app.route("/", methods=["GET", "POST"])
def index():
    """Main page with keyword form and results display."""
    results = None
    keyword = ""
    error = None

    if request.method == "POST":
        keyword = request.form.get("keyword", "").strip()

        if not keyword:
            error = "Vul een zoekwoord in."
        else:
            try:
                results = scrape_people_also_ask(keyword)
                if not results:
                    error = "Geen 'Anderen zochten ook naar' vragen gevonden voor dit zoekwoord."
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template("index.html", results=results, keyword=keyword, error=error)


@app.route("/download-csv", methods=["POST"])
def download_csv():
    """Generate and download CSV file with the scraped results."""
    keyword = request.form.get("keyword", "").strip()

    if not keyword:
        return jsonify({"error": "Geen zoekwoord opgegeven"}), 400

    results = scrape_people_also_ask(keyword)

    if not results:
        return jsonify({"error": "Geen resultaten gevonden"}), 404

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["question", "answer", "source_title", "source_url"])
    writer.writeheader()
    writer.writerows(results)

    # Create response with CSV file
    csv_content = output.getvalue()
    output.close()

    return Response(
        csv_content,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=paa_{keyword.replace(' ', '_')}.csv"
        }
    )


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """API endpoint for programmatic access."""
    data = request.get_json()
    keyword = data.get("keyword", "").strip() if data else ""

    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400

    results = scrape_people_also_ask(keyword)

    return jsonify({
        "keyword": keyword,
        "results": results,
        "count": len(results)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
