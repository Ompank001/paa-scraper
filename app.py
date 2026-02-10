import os
import csv
import io
import re
import json
import base64
from flask import Flask, render_template, request, Response, jsonify
from serpapi import GoogleSearch
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from urllib.parse import urlparse

# Google Sheets imports
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

app = Flask(__name__)

# Get API keys from environment variables
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Google Sheets configuration
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
GOOGLE_SPREADSHEET_ID = os.environ.get("GOOGLE_SPREADSHEET_ID", "")

# WordPress configuration for each site
WP_SITES = {
    "finerbrew.com": {
        "url": os.environ.get("WP_FINERBREW_URL", "https://finerbrew.com"),
        "user": os.environ.get("WP_FINERBREW_USER", ""),
        "password": os.environ.get("WP_FINERBREW_APP_PASSWORD", "")
    },
    "de-koffiekompas.nl": {
        "url": os.environ.get("WP_KOFFIEKOMPAS_URL", "https://de-koffiekompas.nl"),
        "user": os.environ.get("WP_KOFFIEKOMPAS_USER", ""),
        "password": os.environ.get("WP_KOFFIEKOMPAS_APP_PASSWORD", "")
    },
    "de-baardman.nl": {
        "url": os.environ.get("WP_BAARDMAN_URL", "https://de-baardman.nl"),
        "user": os.environ.get("WP_BAARDMAN_USER", ""),
        "password": os.environ.get("WP_BAARDMAN_APP_PASSWORD", "")
    }
}

# Initialize OpenAI client
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize Google Sheets client
sheets_client = None
sheets_init_error = None
if GSPREAD_AVAILABLE and GOOGLE_SHEETS_CREDENTIALS:
    try:
        creds_json = json.loads(GOOGLE_SHEETS_CREDENTIALS)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
        sheets_client = gspread.authorize(credentials)
    except Exception as e:
        sheets_init_error = str(e)
        print(f"Failed to initialize Google Sheets client: {e}")

# Allowed domains for URL search
ALLOWED_DOMAINS = ["finerbrew.com", "de-koffiekompas.nl", "de-baardman.nl"]


def extract_page_info(url):
    """
    Extract H1 header and meta title from a URL.
    Returns a dictionary with h1, meta_title, and extracted keyword.
    """
    try:
        # Validate URL domain
        from urllib.parse import urlparse
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.replace("www.", "")

        if domain not in ALLOWED_DOMAINS:
            raise ValueError(f"Domein niet toegestaan. Gebruik alleen: {', '.join(ALLOWED_DOMAINS)}")

        # Fetch the page
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Parse HTML
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract H1
        h1_tag = soup.find("h1")
        h1_text = h1_tag.get_text(strip=True) if h1_tag else ""

        # Extract meta title
        title_tag = soup.find("title")
        meta_title = title_tag.get_text(strip=True) if title_tag else ""

        # Also try og:title as fallback
        og_title_tag = soup.find("meta", property="og:title")
        og_title = og_title_tag.get("content", "") if og_title_tag else ""

        # Determine main keyword
        keyword = determine_main_keyword(h1_text, meta_title, og_title)

        return {
            "h1": h1_text,
            "meta_title": meta_title,
            "og_title": og_title,
            "keyword": keyword,
            "url": url
        }

    except requests.RequestException as e:
        raise ValueError(f"Kon de URL niet ophalen: {str(e)}")
    except Exception as e:
        raise ValueError(f"Fout bij verwerken URL: {str(e)}")


def determine_main_keyword(h1, meta_title, og_title=""):
    """
    Determine the main keyword from H1 and meta title.
    Priority: H1 > meta title > og:title
    Cleans up common patterns like "| Site Name" from titles.
    """
    # Clean up meta title (remove site name after | or -)
    clean_title = meta_title
    for separator in [" | ", " - ", " – ", " — "]:
        if separator in clean_title:
            clean_title = clean_title.split(separator)[0].strip()

    # Priority: H1 if it's meaningful, otherwise cleaned title
    if h1 and len(h1) > 3:
        keyword = h1
    elif clean_title and len(clean_title) > 3:
        keyword = clean_title
    elif og_title and len(og_title) > 3:
        keyword = og_title
    else:
        keyword = meta_title

    # Clean up the keyword
    # Remove common prefixes/suffixes
    keyword = keyword.strip()

    # Remove numbering like "Top 10", "5 beste"
    keyword = re.sub(r"^(top\s*)?\d+\s+", "", keyword, flags=re.IGNORECASE)

    # Limit length (Google works better with shorter queries)
    words = keyword.split()
    if len(words) > 6:
        keyword = " ".join(words[:6])

    return keyword.strip()


def generate_answer(question, context=""):
    """
    Generate a well-formatted answer for a FAQ question using OpenAI.

    Follows strict PAA writing rules:
    - Direct answer first (1-2 sentences)
    - 40-120 words
    - Neutral, factual tone
    - No first-person, no opinions
    - Clear, simple language
    """
    if not openai_client:
        return None

    try:
        prompt = f"""Je bent een professionele content schrijver. Beantwoord de vraag volgens deze strikte regels:

MANDATORY RULES:

1. DIRECT ANSWER FIRST
   - De eerste 1-2 zinnen moeten de vraag direct en duidelijk beantwoorden
   - Herformuleer de vraag natuurlijk in het antwoord
   - Geen introductie, context-setting, of conclusie
   - Voorbeeld: Vraag "Wat is de beste koffiemachine?" → "De beste koffiemachine is..."

2. CLARITY & SIMPLICITY
   - Gebruik korte, duidelijke zinnen
   - Gebruik simpel, alledaags Nederlands
   - Vermijd jargon tenzij essentieel
   - Leesniveau: algemeen publiek (duidelijk, neutraal, niet-academisch)

3. LENGTH CONTROL
   - Doellengte: 40-120 woorden
   - Verwijder redundantie en opvulling
   - Vul het antwoord niet op om lengte te bereiken

4. NEUTRAL AUTHORITY
   - Schrijf in een feitelijke, kalme, zelfverzekerde toon
   - GEEN eerste persoon ("ik", "wij", "ons")
   - GEEN meningen, hype, of verkooptaal
   - Alleen feiten en nuttige informatie

5. AI-CITATION FRIENDLY
   - Schrijf in complete, op zichzelf staande statements
   - Vermijd vage verwijzingen ("dit", "dat", "zoals hierboven genoemd")
   - Het antwoord moet logisch zijn als het buiten context wordt gelezen

6. FORMATTING
   - Standaard: gewone paragrafen
   - Geen emojis
   - Geen koppen

7. SEO & PAA ALIGNMENT
   - Include natuurlijk de kernvraag of een variant
   - Forceer geen exact-match keywords
   - Vermijd onnatuurlijke herhaling

8. ORIGINALITY
   - Vermijd generieke zinnen die veel voorkomen
   - Voeg duidelijkheid of nuance toe waar mogelijk

Vraag: {question}

{f"Referentie-informatie: {context}" if context else ""}

Antwoord (40-120 woorden, direct, neutraal, zonder eerste persoon):"""

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Je bent een professionele Nederlandse content schrijver die directe, feitelijke, neutrale antwoorden geeft zonder eerste persoon."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=200,
            temperature=0.5
        )

        answer = response.choices[0].message.content.strip()

        # Ensure answer is within 40-120 words
        words = answer.split()
        if len(words) > 120:
            answer = " ".join(words[:120]) + "..."

        return answer

    except Exception as e:
        print(f"Error generating answer: {e}")
        return None


def is_relevant_question(question, keyword):
    """
    Determine if a question is relevant to the keyword.
    Returns True if relevant, False if less relevant (contains brand names, etc.)
    """
    question_lower = question.lower()
    keyword_lower = keyword.lower()

    # Common coffee machine brands (example)
    brand_names = [
        "jura", "delonghi", "de'longhi", "philips", "nespresso", "senseo",
        "dolce gusto", "siemens", "bosch", "melitta", "krups", "saeco",
        "moccamaster", "bialetti", "lavazza", "illy", "sage", "breville",
        # Car brands
        "bmw", "mercedes", "audi", "volkswagen", "toyota", "honda", "ford",
        "tesla", "volvo", "peugeot", "renault", "opel", "kia", "hyundai",
        # Tech brands
        "apple", "samsung", "sony", "lg", "google", "microsoft", "amazon",
        # Add more as needed
    ]

    # Check if question contains the keyword - likely relevant
    keyword_words = keyword_lower.split()
    contains_keyword = any(word in question_lower for word in keyword_words if len(word) > 2)

    # Check if question contains brand names - less relevant if brand not in keyword
    contains_brand = False
    for brand in brand_names:
        if brand in question_lower and brand not in keyword_lower:
            contains_brand = True
            break

    # Relevant if contains keyword and no unrelated brand
    if contains_keyword and not contains_brand:
        return True

    # Less relevant if contains brand not in keyword
    if contains_brand:
        return False

    # Default: if doesn't contain keyword, less relevant
    return contains_keyword


def get_domain_from_url(url):
    """Extract domain from URL, removing www. prefix."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")
    return domain


def get_sheet_for_domain(domain):
    """Get the appropriate sheet/tab for a domain."""
    if not sheets_client or not GOOGLE_SPREADSHEET_ID:
        return None

    try:
        spreadsheet = sheets_client.open_by_key(GOOGLE_SPREADSHEET_ID)

        # Try to get existing sheet for this domain
        try:
            sheet = spreadsheet.worksheet(domain)
        except gspread.WorksheetNotFound:
            # Create new sheet with headers
            sheet = spreadsheet.add_worksheet(title=domain, rows=1000, cols=10)
            sheet.append_row(["URL", "keyword", "PAA", "answer", "publish", "status"])

        return sheet
    except Exception as e:
        print(f"Error getting sheet for domain {domain}: {e}")
        return None


def save_results_to_sheets(url, keyword, results):
    """
    Save PAA results to Google Sheets.

    Args:
        url: The source URL for these results
        keyword: The search keyword used
        results: List of PAA results with question, answer, generated_answer

    Returns:
        Number of rows added, or error message
    """
    if not sheets_client:
        return {"error": "Google Sheets not configured"}

    domain = get_domain_from_url(url) if url else None
    if not domain or domain not in ALLOWED_DOMAINS:
        return {"error": f"Domain not allowed: {domain}"}

    sheet = get_sheet_for_domain(domain)
    if not sheet:
        return {"error": "Could not access spreadsheet"}

    rows_added = 0
    try:
        for result in results:
            question = result.get("question", "")
            # Use generated answer if available, otherwise use scraped answer
            answer = result.get("generated_answer") or result.get("answer", "")

            row = [url, keyword, question, answer, "", ""]  # publish and status empty
            sheet.append_row(row)
            rows_added += 1

        return {"success": True, "rows_added": rows_added}
    except Exception as e:
        return {"error": str(e)}


def get_wp_credentials(domain):
    """Get WordPress credentials for a domain."""
    domain_clean = domain.replace("www.", "")
    if domain_clean in WP_SITES:
        site = WP_SITES[domain_clean]
        if site["user"] and site["password"]:
            return site
    return None


def find_wp_page_by_url(page_url, wp_site):
    """
    Find a WordPress page/post by its URL.
    Returns the page/post ID and type if found.
    """
    parsed = urlparse(page_url)
    slug = parsed.path.strip("/").split("/")[-1]  # Get the last part of the path

    if not slug:
        return None

    # Create auth header
    auth_string = f"{wp_site['user']}:{wp_site['password']}"
    auth_bytes = base64.b64encode(auth_string.encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_bytes}",
        "Content-Type": "application/json"
    }

    api_base = f"{wp_site['url']}/wp-json/wp/v2"

    # Try to find as page first
    try:
        response = requests.get(f"{api_base}/pages?slug={slug}", headers=headers, timeout=10)
        if response.status_code == 200:
            pages = response.json()
            if pages:
                return {"id": pages[0]["id"], "type": "pages", "content": pages[0]["content"]["rendered"]}
    except Exception:
        pass

    # Try to find as post
    try:
        response = requests.get(f"{api_base}/posts?slug={slug}", headers=headers, timeout=10)
        if response.status_code == 200:
            posts = response.json()
            if posts:
                return {"id": posts[0]["id"], "type": "posts", "content": posts[0]["content"]["rendered"]}
    except Exception:
        pass

    return None


def publish_to_wordpress(page_url, question, answer):
    """
    Publish a PAA question and answer to a WordPress page.
    Appends the content at the bottom of the existing page.

    Args:
        page_url: URL of the WordPress page to update
        question: The FAQ question (will be H2)
        answer: The FAQ answer (will be paragraph)

    Returns:
        Success/error dict
    """
    domain = get_domain_from_url(page_url)
    wp_site = get_wp_credentials(domain)

    if not wp_site:
        return {"error": f"WordPress not configured for domain: {domain}"}

    # Find the page
    page_info = find_wp_page_by_url(page_url, wp_site)
    if not page_info:
        return {"error": f"Page not found: {page_url}"}

    # Create new content to append
    faq_html = f"\n\n<h2>{question}</h2>\n<p>{answer}</p>"

    # Append to existing content
    new_content = page_info["content"] + faq_html

    # Update the page
    auth_string = f"{wp_site['user']}:{wp_site['password']}"
    auth_bytes = base64.b64encode(auth_string.encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_bytes}",
        "Content-Type": "application/json"
    }

    api_url = f"{wp_site['url']}/wp-json/wp/v2/{page_info['type']}/{page_info['id']}"

    try:
        response = requests.post(
            api_url,
            headers=headers,
            json={"content": new_content},
            timeout=30
        )

        if response.status_code == 200:
            return {"success": True, "page_id": page_info["id"]}
        else:
            return {"error": f"WordPress API error: {response.status_code} - {response.text}"}
    except Exception as e:
        return {"error": str(e)}


def parse_question(item, keyword="", generate_ai_answer=False):
    """Parse a single question item from SerpAPI response."""
    question = item.get("question", "")

    # Get answer from text_blocks (first paragraph)
    answer = ""
    text_blocks = item.get("text_blocks", [])
    for block in text_blocks:
        if block.get("type") == "paragraph" and block.get("snippet"):
            answer = block.get("snippet", "")
            break

    # Get source from references (first reference)
    source_title = ""
    source_url = ""
    references = item.get("references", [])
    if references:
        first_ref = references[0]
        source_title = first_ref.get("title", "")
        source_url = first_ref.get("link", "")

    # Determine relevance
    relevant = is_relevant_question(question, keyword) if keyword else True

    # Generate AI answer if requested
    generated_answer = None
    if generate_ai_answer and question:
        generated_answer = generate_answer(question, answer)

    return {
        "question": question,
        "answer": answer,
        "generated_answer": generated_answer,
        "source_title": source_title,
        "source_url": source_url,
        "next_page_token": item.get("next_page_token", ""),
        "relevant": relevant
    }


def scrape_people_also_ask(keyword, expand_questions=True, max_results=20, generate_answers=False):
    """
    Get the 'People Also Ask' section from Google.nl using SerpAPI.
    Returns a list of dictionaries with question, answer, and source information.

    Args:
        keyword: Search keyword
        expand_questions: If True, fetch additional questions using next_page_token
        max_results: Maximum number of results to return
        generate_answers: If True, generate AI answers for each question
    """
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY environment variable not set")

    results = []
    seen_questions = set()

    try:
        # Configure search for Dutch Google
        params = {
            "engine": "google",
            "q": keyword,
            "google_domain": "google.nl",
            "gl": "nl",  # Country: Netherlands
            "hl": "nl",  # Language: Dutch
            "api_key": SERPAPI_KEY
        }

        search = GoogleSearch(params)
        data = search.get_dict()

        # Extract "People Also Ask" questions (SerpAPI uses different keys)
        related_questions = data.get("related_questions", [])

        # Also check "people_also_ask" key
        if not related_questions:
            related_questions = data.get("people_also_ask", [])

        # Collect tokens for expansion
        tokens_to_expand = []

        for item in related_questions:
            parsed = parse_question(item, keyword, generate_ai_answer=generate_answers)
            if parsed["question"] and parsed["question"] not in seen_questions:
                seen_questions.add(parsed["question"])
                results.append({
                    "question": parsed["question"],
                    "answer": parsed["answer"],
                    "generated_answer": parsed["generated_answer"],
                    "source_title": parsed["source_title"],
                    "source_url": parsed["source_url"],
                    "relevant": parsed["relevant"]
                })
                if parsed["next_page_token"]:
                    tokens_to_expand.append(parsed["next_page_token"])

        # Expand questions to get more results (uses additional API credits)
        if expand_questions and tokens_to_expand:
            for token in tokens_to_expand[:3]:  # Limit to 3 expansions to save credits
                if len(results) >= max_results:
                    break

                try:
                    expand_params = {
                        "engine": "google_related_questions",
                        "next_page_token": token,
                        "api_key": SERPAPI_KEY
                    }
                    expand_search = GoogleSearch(expand_params)
                    expand_data = expand_search.get_dict()

                    for item in expand_data.get("related_questions", []):
                        if len(results) >= max_results:
                            break
                        parsed = parse_question(item, keyword, generate_ai_answer=generate_answers)
                        if parsed["question"] and parsed["question"] not in seen_questions:
                            seen_questions.add(parsed["question"])
                            results.append({
                                "question": parsed["question"],
                                "answer": parsed["answer"],
                                "generated_answer": parsed["generated_answer"],
                                "source_title": parsed["source_title"],
                                "source_url": parsed["source_url"],
                                "relevant": parsed["relevant"]
                            })
                except Exception:
                    continue  # Skip failed expansions

    except Exception as e:
        print(f"Error during SerpAPI request: {e}")
        raise

    return results


@app.route("/", methods=["GET", "POST"])
def index():
    """Main page with keyword form and results display."""
    results = None
    keyword = ""
    error = None
    generate_answers = False

    if request.method == "POST":
        keyword = request.form.get("keyword", "").strip()
        generate_answers = request.form.get("generate_answers") == "on"

        if not keyword:
            error = "Vul een zoekwoord in."
        elif not SERPAPI_KEY:
            error = "SERPAPI_KEY is niet geconfigureerd. Voeg deze toe aan de environment variables."
        elif generate_answers and not OPENAI_API_KEY:
            error = "OPENAI_API_KEY is niet geconfigureerd. Voeg deze toe om antwoorden te genereren."
        else:
            try:
                results = scrape_people_also_ask(keyword, generate_answers=generate_answers)
                if not results:
                    error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord."
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template(
        "index.html",
        results=results,
        keyword=keyword,
        error=error,
        active_tab="keyword",
        generate_answers=generate_answers,
        openai_configured=bool(OPENAI_API_KEY)
    )


@app.route("/url", methods=["GET", "POST"])
def url_search():
    """URL-based search: extract keyword from page and search PAA."""
    results = None
    url = ""
    keyword = ""
    page_info = None
    error = None
    generate_answers = False

    if request.method == "POST":
        url = request.form.get("url", "").strip()
        generate_answers = request.form.get("generate_answers") == "on"

        if not url:
            error = "Vul een URL in."
        elif not SERPAPI_KEY:
            error = "SERPAPI_KEY is niet geconfigureerd."
        elif generate_answers and not OPENAI_API_KEY:
            error = "OPENAI_API_KEY is niet geconfigureerd. Voeg deze toe om antwoorden te genereren."
        else:
            # Add https:// if missing
            if not url.startswith("http"):
                url = "https://" + url

            try:
                # Extract page info
                page_info = extract_page_info(url)
                keyword = page_info["keyword"]

                if not keyword:
                    error = "Kon geen zoekwoord bepalen uit de pagina."
                else:
                    # Search PAA with extracted keyword
                    results = scrape_people_also_ask(keyword, generate_answers=generate_answers)
                    if not results:
                        error = "Geen 'Mensen vragen ook' vragen gevonden voor dit zoekwoord."

            except ValueError as e:
                error = str(e)
            except Exception as e:
                error = f"Er is een fout opgetreden: {str(e)}"

    return render_template(
        "index.html",
        results=results,
        keyword=keyword,
        url=url,
        page_info=page_info,
        error=error,
        active_tab="url",
        generate_answers=generate_answers,
        openai_configured=bool(OPENAI_API_KEY)
    )


@app.route("/download-csv", methods=["POST"])
def download_csv():
    """Generate and download CSV file with the scraped results."""
    keyword = request.form.get("keyword", "").strip()

    if not keyword:
        return jsonify({"error": "Geen zoekwoord opgegeven"}), 400

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"}), 500

    try:
        results = scrape_people_also_ask(keyword)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not results:
        return jsonify({"error": "Geen resultaten gevonden"}), 404

    # Create CSV in memory
    output = io.StringIO()

    # Check if any result has generated_answer
    has_generated = any(r.get("generated_answer") for r in results)

    if has_generated:
        fieldnames = ["question", "generated_answer", "answer", "source_title", "source_url"]
    else:
        fieldnames = ["question", "answer", "source_title", "source_url"]

    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
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

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"}), 500

    try:
        results = scrape_people_also_ask(keyword)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "keyword": keyword,
        "results": results,
        "count": len(results)
    })


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "serpapi_configured": bool(SERPAPI_KEY)
    })


@app.route("/debug")
def debug():
    """Debug endpoint to see raw SerpAPI response."""
    keyword = request.args.get("q", "hypotheek")

    if not SERPAPI_KEY:
        return jsonify({"error": "SERPAPI_KEY not configured"})

    try:
        params = {
            "engine": "google",
            "q": keyword,
            "google_domain": "google.nl",
            "gl": "nl",
            "hl": "nl",
            "api_key": SERPAPI_KEY
        }

        search = GoogleSearch(params)
        data = search.get_dict()

        # Return relevant parts of the response
        return jsonify({
            "keyword": keyword,
            "has_related_questions": "related_questions" in data,
            "has_people_also_ask": "people_also_ask" in data,
            "related_questions_count": len(data.get("related_questions", [])),
            "people_also_ask_count": len(data.get("people_also_ask", [])),
            "related_questions": data.get("related_questions", []),
            "people_also_ask": data.get("people_also_ask", []),
            "available_keys": list(data.keys()),
            "error": data.get("error")
        })

    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/save-to-sheets", methods=["POST"])
def api_save_to_sheets():
    """
    Save PAA results to Google Sheets.
    Expects JSON with: url, keyword, results (array)
    """
    if not sheets_client:
        return jsonify({"error": "Google Sheets not configured. Set GOOGLE_SHEETS_CREDENTIALS and GOOGLE_SPREADSHEET_ID."}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    url = data.get("url", "")
    keyword = data.get("keyword", "")
    results = data.get("results", [])

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not keyword:
        return jsonify({"error": "Keyword is required"}), 400
    if not results:
        return jsonify({"error": "No results to save"}), 400

    result = save_results_to_sheets(url, keyword, results)

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result)


@app.route("/api/publish", methods=["POST"])
def api_publish():
    """
    Publish a PAA question/answer to WordPress.
    Called by Google Apps Script when publish is set to 'Yes'.

    Expects JSON with: url, question, answer
    Returns: success/error status
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    url = data.get("url", "")
    question = data.get("question", "")
    answer = data.get("answer", "")

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not question:
        return jsonify({"error": "Question is required"}), 400
    if not answer:
        return jsonify({"error": "Answer is required"}), 400

    result = publish_to_wordpress(url, question, answer)

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result)


@app.route("/api/sheets-status")
def api_sheets_status():
    """Check Google Sheets configuration status."""
    # Get raw env var for debugging
    raw_creds = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

    # Try to parse JSON to see if that's the issue
    json_valid = False
    json_error = None
    if raw_creds:
        try:
            json.loads(raw_creds)
            json_valid = True
        except Exception as e:
            json_error = str(e)

    return jsonify({
        "gspread_available": GSPREAD_AVAILABLE,
        "credentials_configured": bool(GOOGLE_SHEETS_CREDENTIALS),
        "credentials_length": len(raw_creds) if raw_creds else 0,
        "credentials_starts_with": raw_creds[:20] if raw_creds else "",
        "json_valid": json_valid,
        "json_error": json_error,
        "spreadsheet_id_configured": bool(GOOGLE_SPREADSHEET_ID),
        "spreadsheet_id": GOOGLE_SPREADSHEET_ID[:10] + "..." if GOOGLE_SPREADSHEET_ID else "",
        "sheets_client_ready": sheets_client is not None,
        "sheets_init_error": sheets_init_error
    })


@app.route("/api/wp-status")
def api_wp_status():
    """Check WordPress configuration status for all sites."""
    status = {}
    for domain, config in WP_SITES.items():
        status[domain] = {
            "url": config["url"],
            "user_configured": bool(config["user"]),
            "password_configured": bool(config["password"])
        }
    return jsonify(status)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

