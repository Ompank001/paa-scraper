import os
import csv
import io
import time
import urllib.parse
from flask import Flask, render_template, request, Response, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException, ElementClickInterceptedException
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
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=nl-NL")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

    # Exclude automation flags
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Check for Railway/Docker environment
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    # Use ChromeDriverManager for automatic driver management
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Additional stealth
    driver.execute_cdp_cmd('Network.setUserAgentOverride', {
        "userAgent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    })

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

        # URL encode the keyword properly
        encoded_keyword = urllib.parse.quote_plus(keyword)

        # Navigate to Google Netherlands with Dutch language settings
        search_url = f"https://www.google.nl/search?q={encoded_keyword}&hl=nl&gl=nl"
        driver.get(search_url)

        # Wait for page to load
        time.sleep(3)

        # Handle cookie consent dialog (GDPR) - Dutch version
        try:
            # Try multiple cookie consent button selectors
            cookie_selectors = [
                "//button[contains(., 'Alles accepteren')]",
                "//button[contains(., 'Accepteren')]",
                "//button[contains(., 'Alle cookies accepteren')]",
                "//div[contains(., 'Alles accepteren')]/ancestor::button",
                "//button[@id='L2AGLb']",  # Common Google consent button ID
                "//button[contains(@class, 'tHlp8d')]",
            ]

            for selector in cookie_selectors:
                try:
                    cookie_btn = driver.find_element(By.XPATH, selector)
                    if cookie_btn and cookie_btn.is_displayed():
                        cookie_btn.click()
                        time.sleep(2)
                        break
                except (NoSuchElementException, ElementClickInterceptedException):
                    continue
        except Exception:
            pass

        # Wait for search results to load
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "search"))
            )
        except TimeoutException:
            pass

        # Find PAA questions using multiple strategies
        paa_questions = []

        # Strategy 1: Look for elements with data-q attribute (question text)
        try:
            questions_with_data_q = driver.find_elements(By.CSS_SELECTOR, "[data-q]")
            for q in questions_with_data_q:
                question_text = q.get_attribute("data-q")
                if question_text:
                    paa_questions.append({"element": q, "question": question_text})
        except Exception:
            pass

        # Strategy 2: Look for accordion-style PAA elements
        if not paa_questions:
            try:
                # Find all expandable question divs
                expandable_elements = driver.find_elements(By.CSS_SELECTOR, "div[jsname][data-hveid] div[role='button']")
                for elem in expandable_elements:
                    try:
                        question_text = elem.text.strip()
                        if question_text and "?" in question_text:
                            paa_questions.append({"element": elem.find_element(By.XPATH, "./ancestor::div[@jsname]"), "question": question_text})
                    except Exception:
                        continue
            except Exception:
                pass

        # Strategy 3: Look for divs containing questions with specific structure
        if not paa_questions:
            try:
                # Look for the PAA container by finding "Mensen vragen ook" or similar headers
                paa_headers = driver.find_elements(By.XPATH,
                    "//*[contains(text(), 'Mensen vragen ook') or contains(text(), 'Gerelateerde vragen') or contains(text(), 'Anderen zochten ook')]"
                )

                for header in paa_headers:
                    try:
                        # Get parent container
                        container = header.find_element(By.XPATH, "./ancestor::div[@jscontroller]")
                        # Find all question-like elements within
                        question_divs = container.find_elements(By.CSS_SELECTOR, "div[data-hveid]")
                        for qd in question_divs:
                            text = qd.text.split('\n')[0] if qd.text else ""
                            if text and len(text) > 10:
                                paa_questions.append({"element": qd, "question": text})
                    except Exception:
                        continue
            except Exception:
                pass

        # Strategy 4: Generic approach - find all clickable question-like elements
        if not paa_questions:
            try:
                all_divs = driver.find_elements(By.CSS_SELECTOR, "div[jscontroller][jsaction*='click']")
                for div in all_divs:
                    try:
                        text = div.text.strip()
                        # Check if it looks like a question
                        if text and ("?" in text or text.endswith("?")) and len(text) < 200:
                            first_line = text.split('\n')[0]
                            if first_line not in [q["question"] for q in paa_questions]:
                                paa_questions.append({"element": div, "question": first_line})
                    except Exception:
                        continue
            except Exception:
                pass

        # Process found questions
        for paa in paa_questions[:10]:  # Limit to 10 questions
            try:
                question_text = paa["question"]
                element = paa["element"]

                # Try to click and expand
                try:
                    element.click()
                    time.sleep(0.8)
                except Exception:
                    pass

                # Try to get the answer
                answer_text = ""
                try:
                    # Look for answer content after expansion
                    answer_selectors = [
                        "[data-attrid='wa:/description'] span",
                        "div[data-md] span",
                        ".mod div span[lang]",
                        "div[style*='overflow'] span",
                    ]
                    for sel in answer_selectors:
                        try:
                            answer_elem = element.find_element(By.CSS_SELECTOR, sel)
                            if answer_elem.text:
                                answer_text = answer_elem.text
                                break
                        except NoSuchElementException:
                            continue
                except Exception:
                    pass

                # Try to get source URL and title
                source_url = ""
                source_title = ""
                try:
                    link = element.find_element(By.CSS_SELECTOR, "a[href*='http']")
                    source_url = link.get_attribute("href")
                    try:
                        source_title = link.find_element(By.CSS_SELECTOR, "h3").text
                    except NoSuchElementException:
                        source_title = link.text.split('\n')[0] if link.text else ""
                except NoSuchElementException:
                    pass

                # Only add if we have a valid question
                if question_text and len(question_text) > 5:
                    # Avoid duplicates
                    if not any(r["question"] == question_text for r in results):
                        results.append({
                            "question": question_text,
                            "answer": answer_text,
                            "source_title": source_title,
                            "source_url": source_url
                        })

            except Exception:
                continue

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
                    error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord. Probeer een ander zoekwoord."
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


@app.route("/debug", methods=["GET"])
def debug():
    """Debug endpoint to check what Google returns."""
    keyword = request.args.get("q", "hypotheek")
    driver = None
    debug_info = {}

    try:
        driver = get_chrome_driver()
        encoded_keyword = urllib.parse.quote_plus(keyword)
        search_url = f"https://www.google.nl/search?q={encoded_keyword}&hl=nl&gl=nl"
        driver.get(search_url)
        time.sleep(3)

        # Get page info
        debug_info["title"] = driver.title
        debug_info["url"] = driver.current_url
        debug_info["page_source_length"] = len(driver.page_source)

        # Check for common elements
        debug_info["has_search_box"] = len(driver.find_elements(By.NAME, "q")) > 0
        debug_info["has_results"] = len(driver.find_elements(By.ID, "search")) > 0

        # Check for PAA indicators
        debug_info["data_q_elements"] = len(driver.find_elements(By.CSS_SELECTOR, "[data-q]"))
        debug_info["jscontroller_elements"] = len(driver.find_elements(By.CSS_SELECTOR, "div[jscontroller]"))

        # Look for PAA text
        page_text = driver.page_source.lower()
        debug_info["contains_mensen_vragen"] = "mensen vragen ook" in page_text
        debug_info["contains_gerelateerde"] = "gerelateerde vragen" in page_text

    except Exception as e:
        debug_info["error"] = str(e)
    finally:
        if driver:
            driver.quit()

    return jsonify(debug_info)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
