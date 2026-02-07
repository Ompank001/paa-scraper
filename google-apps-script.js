/**
 * Google Apps Script for PAA Scraper - WordPress Publishing
 *
 * SETUP INSTRUCTIONS:
 * 1. Open your Google Spreadsheet
 * 2. Go to Extensions > Apps Script
 * 3. Delete any existing code and paste this entire file
 * 4. Update the API_URL below with your Railway app URL
 * 5. Save the project (Ctrl+S)
 * 6. Click "Run" and authorize the script when prompted
 * 7. Go to Triggers (clock icon) > Add Trigger
 *    - Choose function: onEditTrigger
 *    - Event source: From spreadsheet
 *    - Event type: On edit
 *    - Save
 *
 * SPREADSHEET STRUCTURE (each tab):
 * Column A: URL (page URL)
 * Column B: keyword
 * Column C: PAA (the question)
 * Column D: answer
 * Column E: publish (set to "Yes" to trigger)
 * Column F: status (automatically set to "done" after publishing)
 */

// UPDATE THIS URL to your Railway app URL
const API_URL = 'https://paa-scraper-production.up.railway.app/api/publish';

/**
 * Trigger function that runs when any cell is edited.
 * Checks if the "publish" column (E) was changed to "Yes" and publishes to WordPress.
 */
function onEditTrigger(e) {
  try {
    const sheet = e.source.getActiveSheet();
    const range = e.range;
    const row = range.getRow();
    const col = range.getColumn();

    // Only process if:
    // - Not the header row (row 1)
    // - Column E (publish) was edited
    // - Value is "Yes" (case insensitive)
    if (row <= 1 || col !== 5) {
      return;
    }

    const newValue = range.getValue().toString().toLowerCase().trim();
    if (newValue !== 'yes' && newValue !== 'ja') {
      return;
    }

    // Check if status is already "done"
    const statusCell = sheet.getRange(row, 6);
    if (statusCell.getValue().toString().toLowerCase() === 'done') {
      return; // Already published
    }

    // Get the row data
    const rowData = sheet.getRange(row, 1, 1, 6).getValues()[0];
    const url = rowData[0];      // Column A
    const keyword = rowData[1];  // Column B
    const question = rowData[2]; // Column C
    const answer = rowData[3];   // Column D

    // Validate required fields
    if (!url || !question || !answer) {
      statusCell.setValue('error: missing data');
      return;
    }

    // Set status to "publishing..."
    statusCell.setValue('publishing...');
    SpreadsheetApp.flush();

    // Call the API
    const result = publishToWordPress(url, question, answer);

    if (result.success) {
      statusCell.setValue('done');
    } else {
      statusCell.setValue('error: ' + result.error);
    }

  } catch (error) {
    Logger.log('Error in onEditTrigger: ' + error.toString());
  }
}

/**
 * Calls the PAA Scraper API to publish content to WordPress.
 */
function publishToWordPress(url, question, answer) {
  try {
    const payload = {
      url: url,
      question: question,
      answer: answer
    };

    const options = {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };

    const response = UrlFetchApp.fetch(API_URL, options);
    const responseCode = response.getResponseCode();
    const responseText = response.getContentText();

    if (responseCode === 200) {
      const data = JSON.parse(responseText);
      return { success: true, data: data };
    } else {
      const errorData = JSON.parse(responseText);
      return { success: false, error: errorData.error || 'Unknown error' };
    }

  } catch (error) {
    return { success: false, error: error.toString() };
  }
}

/**
 * Manual function to test the API connection.
 * Run this from the Apps Script editor to verify setup.
 */
function testApiConnection() {
  const testPayload = {
    url: 'https://example.com/test',
    question: 'Test question',
    answer: 'Test answer'
  };

  const options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(testPayload),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(API_URL, options);
    Logger.log('Response code: ' + response.getResponseCode());
    Logger.log('Response: ' + response.getContentText());
  } catch (error) {
    Logger.log('Error: ' + error.toString());
  }
}

/**
 * Creates the initial spreadsheet structure with tabs for each domain.
 * Run this once to set up the spreadsheet.
 */
function setupSpreadsheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const domains = ['finerbrew.com', 'de-koffiekompas.nl', 'de-baardman.nl'];
  const headers = ['URL', 'keyword', 'PAA', 'answer', 'publish', 'status'];

  domains.forEach(function(domain) {
    let sheet;
    try {
      sheet = ss.getSheetByName(domain);
    } catch (e) {
      sheet = null;
    }

    if (!sheet) {
      sheet = ss.insertSheet(domain);
    }

    // Set headers if first row is empty
    if (!sheet.getRange(1, 1).getValue()) {
      sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
      sheet.getRange(1, 1, 1, headers.length).setFontWeight('bold');
      sheet.setFrozenRows(1);

      // Set column widths
      sheet.setColumnWidth(1, 250); // URL
      sheet.setColumnWidth(2, 150); // keyword
      sheet.setColumnWidth(3, 300); // PAA
      sheet.setColumnWidth(4, 400); // answer
      sheet.setColumnWidth(5, 80);  // publish
      sheet.setColumnWidth(6, 100); // status
    }
  });

  Logger.log('Spreadsheet setup complete!');
}
