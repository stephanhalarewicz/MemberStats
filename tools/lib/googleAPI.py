import os
import base64
import pickle
import traceback
from ssl import SSLError

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.exceptions import RefreshError

from util import print_error
try:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
except ImportError, ie:
    print_error("Import Error: google.auth not found. See steps below")
    print "Install the google.auth modules by typing. " \
          "'pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib'" \
          "into the command line then re-run stats"
    raise ie


# Obtain Authentication or Credentials to access Google Sheets

# If modifying these scopes, delete your previously saved credentials
# at ~/.credentials/token.pickle and support_token.pickle
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/gmail.compose']
SUPPORT_SCOPE = ['https://www.googleapis.com/auth/gmail.readonly']
GOV_SUPPORT_SCOPE = ['https://www.googleapis.com/auth/gmail.readonly']
CLIENT_SECRET_FILE = 'tools/client_secret.json'
APPLICATION_NAME = 'Google API for Stats'


def _get_credentials(account_type, scope):
    """
    Gets valid user credentials from storage.

    If nothing has been stored, or if the stored credentials are invalid,
    the OAuth2 flow is completed to obtain the new credentials.

    :param account_type: ['personal', 'support', 'gov'] specifies account type for which credentials should be obtained
    :return: Credentials, the obtained credential.
    """
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is created automatically when the
    # authorization flow completes for the first time.
    home_dir = os.path.expanduser('~')
    credential = '/' + account_type + '_token.pickle'

    credential_dir = os.path.join(home_dir, '.credentials')
    credential_path = credential_dir + credential
    if not os.path.exists(credential_dir):
        os.makedirs(credential_dir)
    if os.path.exists(credential_path):
        with open(credential_path, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError, e:
                # Catch any problems refreshing the existing credential. This will not catch refresh errors if the
                # user has changed their password in the last six hours. Manually delete the credential in those cases.
                raw_input("Unable to authenticate. Deleting existing credential. Please re-authenticate: " +
                          account_type + ". Press enter to continue with authentication.")
                os.remove(credential_path)
                raise e
        else:
            raw_input("You will now be asked to authenticate access for a " + account_type + " google account."
                      "Please log into the appropriate account when prompted. Press enter to continue.")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRET_FILE, scope)
            creds = flow.run_local_server()
        # Save the credentials for the next run
        with open(credential_path, 'wb') as token:
            pickle.dump(creds, token)

    return creds


def remove_credentials(account_type):
    home_dir = os.path.expanduser('~')
    credential = '/' + account_type + '_token.pickle'

    credential_dir = os.path.join(home_dir, '.credentials')
    credential_path = credential_dir + credential

    os.remove(credential_path)


def get_api(name, version, account_type, scope, timeout):
    """
    Retrieves the specified google api for the specified account type
    :param name: name of the api being requested (i.e. gmail, sheets)
    :param version: version of the requested api
    :param account_type: ['personal', 'support', 'gov'] specifies account for which api should be obtained
    :param scope: Access scope for the api service
    :param timeout: number of attempts to access the api
    :return: google api service. If an invalid type is provided None is returned.
    """

    if timeout == 0:
        raise RuntimeError("Unable to obtain authentication credentials. Please contact Stephan")
    try:
        return build(name, version, credentials=_get_credentials(account_type, scope))
    except RefreshError:
        return get_api(name, version, account_type, scope, timeout - 1)


def get_range(rng, sheet_id, sheet_api, dimension='ROWS', values_only=True):
    """
    Obtains a list of values for the given spreadsheet range
    :param rng: Valid range in A1 notation or a defined named range
    :param sheet_id: Sheet from which then range is obtained.
    :param sheet_api: Sheets API service used
    :param dimension: dimension for representing the data in a list. (default = 'ROWS')
    :param values_only: True if only a list of dimension values should be returned else a ValueRange object is
    returned (default = True)
    :return: a list of either rows or columns depending on the dimension used.
    """
    try:
        if values_only:
            return sheet_api.spreadsheets().values().get(spreadsheetId=sheet_id, range=rng,
                                                         majorDimension=dimension).execute().get('values', [])
        else:
            return sheet_api.spreadsheets().values().get(spreadsheetId=sheet_id, range=rng,
                                                         majorDimension=dimension).execute()
    except HttpError, e:
        print_error('Error: Could not get range: ' + str(rng) + ' from sheet ' + str(sheet_id))
        raise e


def _new_cell(v, t):
    """
    Creates a new CellData object
    :param v, t: (value, type)
        A tuple consisting of (value, type).
        type: 'DATE', 'NUMBER', 'STRING'. If type is not specified or not one of the types specifed the value will be
        written as a string.
        DATE values must be given as a serial number. This can be calculated as the days since 12/30/1899. With partial
        days counting as a decimal.
    :return: a CellData object
    """
    cell_value = {}
    if t == 'DATE':
        cell_value['userEnteredValue'] = {'numberValue': v}
        cell_value['userEnteredFormat'] = {"numberFormat": {"type": "DATE"}}
    elif t == 'NUMBER':
        cell_value['userEnteredValue'] = {'numberValue': v}
    else:
        cell_value['userEnteredValue'] = {'stringValue': v}

    return cell_value


def _new_row(values):
    """
    Creates a new RowData object from a list of values.
    :param values: (value, type)
        A list of tuples consisting of (value, type).
        type: 'DATE', 'NUMBER', 'STRING'
    :return: A new RowData object.
    """
    row_values = []
    for (value, t) in values:
        cell = _new_cell(value, t)
        row_values.append(cell)

    return {'values': row_values}


def _new_value_range(rng):
    """
    Creates a 2D array of values from the values in range.
    :param rng: lst(lst(values)) representing to be entered
    :return: lst(RowData) representing the new range.
    """
    range_values = []
    for row in rng:
        range_values.append(_new_row(row))
    return range_values


def insert_column_request(sheet_id, values, start_row, end_row, start_col, end_col, inherit=False):
    """
    Creates two spreadsheets.batchUpdate requests that insert a new column into the specified sheet.
    :param sheet_id: Sheet ID for the sheet on which the column will be inserted.
    :param values: (value, type)
        A list of tuples consisting of (value, type).
        type: 'DATE', 'NUMBER', 'STRING'
    :param start_row: int start row inclusive of range that should be sorted.
    :param end_row: int end row exclusive of range that should be sorted.
    :param start_col: int start column inclusive of range that should be sorted.
    :param end_col: int end column exclusive of range that should be sorted.
    :param inherit: boolean Whether column properties should be extended from the column before or
     after the newly inserted column.
    :return:
    """
    two_d_values = []
    for value in values:
        two_d_values.append([value])

    # column = _new_value_range(two_d_values)
    insert_request = {
            "insertDimension": {
                "inheritFromBefore": inherit,
                "range": {
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                    "endIndex": end_col,
                    "sheetId": sheet_id
                }
            }
        }
    update = update_request(sheet_id, two_d_values, start_row, end_row, start_col, end_col)

    return [insert_request, update]


def _create_row(values):
    return {
        "values": values,
        "majorDimension": "ROWS"
    }


def update_range(service, spreadsheet_id, rng, values,
                 value_input='USER_ENTERED', value_render='FORMATTED_VALUE'):
    """
    Attempts to update the specified range
    :param service: Authorized Google Sheets service
    :param spreadsheet_id: Spreadsheet ID for the sheet that should be updated.
    :param rng: A1 Notation range for sheet that will be updated.
    :param values: New values
    :param value_input: str Determines how input data should be interpreted (default='USER_ENTERED'
     'RAW' : The values the user has entered will not be parsed and will be stored as-is.
     'USER_ENTERED' : The values will be parsed as if the user typed them into the UI.
    :param value_render: str Determines how values should be rendered in the output. (default='FORMATTED_VALUE'
        'FORMATTED_VALUE' : Values will be calculated & formatted according to the cell's formatting
        'UNFORMATTED_VALUE' : Values will be calculated, but not formatted.
        'FORMULA' : Values will not be calculated.
    :return:
    """
    request_body = _create_row(values)
    try:
        service.spreadsheets().values().update(spreadsheetId=spreadsheet_id, body=request_body,
                                               range=rng, valueInputOption=value_input,
                                               responseValueRenderOption=value_render).execute()
    except HttpError, e:
        print_error('Error: Failed to update range: ' + str(rng) + ' on sheet: ' + str(spreadsheet_id))
        raise e


def update_request(sheet_id, rng, start_row, end_row, start_col, end_col, ):
    """
    Creates a spreadsheets.batchUpdate request to update the specified range
    :param sheet_id: str Sheet on which data will be sorted.
    :param rng: RowData object containing data about each cell in a row.
    :param start_row: int start row inclusive of range that should be sorted.
    :param end_row: int end row exclusive of range that should be sorted.
    :param start_col: int start column inclusive of range that should be sorted.
    :param end_col: int end column exclusive of range that should be sorted.
    :return: updateCells request
    """
    row_data = _new_value_range(rng)
    return {
      "updateCells": {
        "rows": row_data,
        "fields": "*",
        "range": {
                   "sheetId": sheet_id,
                   "startRowIndex": start_row,
                   "endRowIndex": end_row,
                   "startColumnIndex": start_col,
                   "endColumnIndex": end_col
               }
      }
    }


def sort_request(sheet_id, sort_index, start_row, end_row, start_col, end_col, order='ASCENDING'):
    """
    Creates a spreadsheets.batchUpdate request that sorts data in rows based on a sort order per column.
    :param sheet_id: str Sheet on which data will be sorted.
    :param sort_index: int the column which should be sorted
    :param start_row: int start row inclusive of range that should be sorted.
    :param end_row: int end row exclusive of range that should be sorted.
    :param start_col: int start column inclusive of range that should be sorted.
    :param end_col: int end column exclusive of range that should be sorted.
    :param order: str 'ASCENDING' | 'DESCENDING'
    :return: A sortRange Request
    """
    return {"sortRange": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row,
                    "startColumnIndex": start_col,
                    "endColumnIndex": end_col
                },
                "sortSpecs": [
                    {
                        "dimensionIndex": sort_index,
                        "sortOrder": order
                    }
                ]
            }}


def duplicate_sheet_request(sheet_id, new_title, insert_index):
    """
    Constructs a spreadsheets.batchUpdate request to duplicate the contents of a sheet
    :param sheet_id: The sheet to duplicate
    :param new_title: Title of the duplicated sheet
    :param insert_index: The zero-based index where the new sheet should be inserted. The index of all sheets after
    this will be incremented.
    :return: A duplicateSheet request
    """
    return {'duplicateSheet': {
        'sourceSheetId': sheet_id,
        'insertSheetIndex': insert_index,
        'newSheetName': new_title
        }
    }


def delete_named_range_request(service, spreadsheet_id, rng):
    """
    Construct a series of spreadsheets.batchUpdate requests to remove the named ranges with the given name from
    the spreadsheet.
    :param service: Authorized Google Sheets service
    :param spreadsheet_id: str The spreadsheet to apply the updates to.
    :param rng: lst(str) List of named ranges to be removed.
    :return: A list of deleteNamedRange requests
    :raise HttpError: If any of the ranges are not present in the sheet.
    """
    try:
        named_ranges = service.spreadsheets().get(spreadsheetId=spreadsheet_id, ranges=rng).execute().get(
            'namedRanges', [])

        request_body = []
        for named_range in named_ranges:
            range_id = named_range['namedRangeId']
            request_body.append({"deleteNamedRange": {"namedRangeId": range_id}})

        return request_body
    except HttpError, e:
        print_error('Error: Failed to retrieve named ranges from range: ' + str(rng) +
                    'on sheet: ' + str(spreadsheet_id))
        raise e


def spreadsheet_batch_update(service, spreadsheet_id, requests):
    """
    Applies one or more updates to the specified sheet using an authorized service.
    :param service: Authorized Google Sheets service
    :param spreadsheet_id: str The spreadsheet to apply the updates to.
    :param requests: A list of updates to apply to the spreadsheet. Requests are applied in the order specified. If
    any request fails or is not valid, none of the updates will be applied.
    :return: None
    :raise HttpError: If an any update fails.
    """
    request_body = {'requests': requests}

    try:
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=request_body).execute()
    except HttpError, e:
        request_names = []
        for request in requests:
            request_names.append(request.keys())
        print_error('Error: Batch update failed. Sheet: ' + str(spreadsheet_id) + ', Requests: ' + str(request_names))
        raise e


def remove_formulas(service, spreadsheet_id, rng):
    values = get_range(rng, spreadsheet_id, service, 'COLUMNS', False)
    try:
        service.spreadsheets().values().update(spreadsheetId=spreadsheet_id, valueInputOption='RAW',
                                               range=values['range'], body=values).execute()
    except HttpError, e:
        print_error('Error: Failed to remove formulas from range: ' + str(rng) + 'on sheet: ' + str(spreadsheet_id))
        raise e


def clear_ranges(service, spreadsheet_id, ranges):
    """
    Clears the specified ranges.
    :param service: Authorized Google Sheets service
    :param spreadsheet_id: Spreadsheet on which the ranges will be cleared.
    :param ranges: Ranges to be cleared in A1 Notation
    :return:
    """
    request_body = {'ranges': ranges}
    try:
        service.spreadsheets().values().batchClear(spreadsheetId=spreadsheet_id, body=request_body).execute()
    except HttpError, e:
        print_error('Error: Failed to clear ranges: ' + str(ranges) + 'on sheet: ' + str(spreadsheet_id))
        raise e


# Mail API Methods
def _create_message(sender, to, subject, body):
    """
    Creates a message for use in the gmail api. Must create a draft before sending.
    :param sender: Message 'From' email
    :param to: Message 'To' email
    :param subject: Subject line of the message
    :param body: body of the message using email.mime format.
    :return: a new message
    """
    message = body
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    return {'raw': base64.urlsafe_b64encode(message.as_string())}


def _create_draft(service, user_id, message_body):
    """
    Creates a draft from the provided message
    :param service: Mail API used to access Gmail
    :param user_id: user for who draft will be created.
    :param message_body:
    :return: new gmail draft or None if draft creation fails
    """
    try:
        message = {'message': message_body}
        draft = service.users().drafts().create(userId=user_id, body=message).execute()
        return draft
    except HttpError as error:
        print_error('Error: Failed to create. Please see stats_email.txt for draft and send manually.')
        raise error


def _send_draft(service, user_id, draft):
    """
    Sends the given draft.
    :param service: Mail API used to send the draft
    :param user_id: User who owns te draft
    :param draft:  Draft to be sent
    :return: None
    """
    try:
        service.users().drafts().send(userId=user_id, body={'id': draft['id']}).execute()
    except HttpError, e:
        print_error('Error: Failed to send draft')
        raise e


def send_message(service, sender, to, subject, text):
    """
    Sends an email using the Mail API with the given information.
    :param service: Mail API used to create and send the message
    :param sender: Message From email. User must be have accesss to this address.
                    Special value 'me' uses the authenticated user.
    :param to: Message To email
    :param subject: Email subject line
    :param text: Body of the email.
    :return:
    """
    try:
        message = _create_message(sender, to, subject, text)
        draft = _create_draft(service, sender, message)
        _send_draft(service, sender, draft)
    except HttpError:
        try:
            with open('email.txt', 'w') as out:
                out.write(subject)
                out.write(text)
        except IOError:
            print_error('Error: Failed write to text file. See text below.')
            print subject
            print text


def get_labels(service, user_id='me'):
    """
    Obtain a dictionary of label IDs mapped to their respective label name.
    :param service: Mail API service used to obtain the lables. Must have read access to user's mail
    :param user_id: default to 'me'
    :return: dictionary (key, value) = (id, name)
    """
    try:
        response = service.users().labels().list(userId=user_id).execute()
        labels = {}

        for item in response['labels']:
            labels[item['id']] = item['name']

        return labels
    except HttpError, e:
        print_error('Error: Failed to retrieve labels for user: ' + str(user_id))
        raise e


def get_message(service, user_id, msg_id, labels):
    """
    Gets the specified message
    :param service: Mail API service used to obtain the messages. Must have read access to user's mail
    :param user_id: default to 'me':
    :param msg_id: message to be fetched
    :param labels: dictionary mapping label ids to label name
    :return: message in JSON format containing 'X-GM-THRID', 'X-Gmail-Labels', 'To', 'From', 'Subject', 'Date'
    """
    to_find = ['To', 'From', 'Subject', 'Date']
    try:
        response = service.users().messages().get(
            userId=user_id, id=msg_id, format='metadata', metadataHeaders=['From', 'To', 'Date', 'Subject']).execute()
        message = {'X-GM-THRID': response['threadId'], 'X-Gmail-Labels': response['labelIds']}
        message['X-Gmail-Labels'] = map(lambda l: labels[l], message['X-Gmail-Labels'])

        while len(to_find) > 0:
            found = next(header for header in response['payload']['headers'] if header['name'] in to_find)
            encoded = found['value'].encode('ascii', 'ignore')
            message[found['name']] = encoded
            to_find.remove(found['name'])

        return message
    except SSLError, e:
        print "Timeout"
        print msg_id
        print e.__class__
        print e
        traceback.print_exc()

    except StopIteration:
        # Skip this message. This only happens with drafts missing a field and does not affect stats.
        pass
    except HttpError, e:
        print_error('Error: Failed to retrieve message: ' + msg_id)
        raise e


def get_messages(service, user_id='me', query=''):
    """
    Obtains a list of gmail messages containing Thread ID, Subject, To, From and Labels
    :param service: Mail API service used to obtain the messages. Must have read access to user's mail
    :param user_id: default to 'me'
    :param query:
    :return: list of messages. message.keys() = 'X-GM-THRID' , Subject, To, From and 'X-Gmail-Labels'
    """
    try:
        response = service.users().messages().list(userId=user_id, q=query).execute()
        messages = []
        if 'messages' in response:
            messages.extend(response['messages'])

        while 'nextPageToken' in response:
            page_token = response['nextPageToken']
            response = service.users().messages().list(userId=user_id, q=query,
                                                       pageToken=page_token).execute()
            messages.extend(response['messages'])
        labels = get_labels(service, user_id)
        result = []
        for message in messages:
            new_message = get_message(service, user_id, message['id'], labels)
            if new_message is not None:
                result.append(new_message)
        return result

    except HttpError, e:
        print_error('Error: Failed to retrieve messages for: ' + str(user_id) + ' using query: ' + str(query))
        raise e


def get_thread_ids(service, user_id='me', query=''):
    """
    Obtains a list of all gmail threadsIds where the thread contains at least one message matching the query.
    :param service: Mail API service used to obtain the messages. Must have read access to user's mail
    :param user_id: default to 'me'
    :param query: gmail query used to search for messages.
    :return: list of messages. message.keys() = 'X-GM-THRID' , Subject, To, From and 'X-Gmail-Labels'
    """
    try:
        response = service.users().messages().list(userId=user_id, q=query).execute()
        thread_ids = set()

        while 'nextPageToken' in response:
            # Read threadIds on current page.
            for message in response['messages']:
                thread_ids.add(message["threadId"])

            # Advance to next page.
            page_token = response['nextPageToken']
            response = service.users().messages().list(userId=user_id, q=query,
                                                       pageToken=page_token).execute()
        # Get threadIds from last page.
        if 'messages' in response:
            for message in response['messages']:
                thread_ids.add(message["threadId"])

        return thread_ids

    except HttpError, e:
        print_error('Error: Failed to retrieve messages for: ' + str(user_id) + ' using query: ' + str(query))
        raise e


def get_messages_from_thread_ids(service, threads, user_id='me'):
    """
    Obtains every message in the specified threadIds
    :param service: Mail API service used to obtain the messages. Must have read access to user's mail
    :param threads: list of gmail threadIds
    :param user_id: default to 'me'
    :return: list of messages. message.keys() = 'X-GM-THRID' , Subject, To, From and 'X-Gmail-Labels'
    """
    try:
        result = []
        labels = get_labels(service, user_id)
        for thread in threads:
            thread_response = service.users().threads().get(userId='me', id=thread).execute()
            messages = thread_response["messages"]
            for message in messages:
                new_message = get_message(service, user_id, message['id'], labels)
                if new_message is not None:
                    result.append(new_message)
        return result

    except HttpError, e:
        print_error('Error: Failed to retrieve messages from thread ids for: ' + str(user_id))
        raise e


def get_messages_from_threads(service, user_id='me', query=''):
    """
    Obtains a list of gmail messages containing Thread ID, Subject, To, From and Labels
    :param service: Mail API service used to obtain the messages. Must have read access to user's mail
    :param user_id: default to 'me'
    :param query: gmail query used to search for messages. All messages in thread must match query.
    :return: list of messages. message.keys() = 'X-GM-THRID' , Subject, To, From and 'X-Gmail-Labels'
    """
    # try:
    # response has format {"threads": [threadResource], "resultSizeEstimate": 1, "nextPageToken": "xxxx"}
    # threadResource has format {"id": "xxxx", "snippet": "xxxxx", "historyId": "xxxxx"}
    response = service.users().threads().list(userId=user_id, q=query).execute()
    threads = set()
    if 'threads' in response:
        for thread in response['threads']:
            threads.add(thread["id"])

    while 'nextPageToken' in response:
        page_token = response['nextPageToken']
        response = service.users().threads().list(userId=user_id, q=query,
                                                  pageToken=page_token).execute()
        for thread in response['threads']:
            threads.add(thread["id"])

    labels = get_labels(service, user_id)
    result = []

    for thread in threads:
        try:
            # print thread # this is the thread id
            # TODO Multithread this for speed
            thread_response = service.users().threads().get(userId='me', id=thread, format="minimal").execute()
            messages = thread_response["messages"]
            for message in messages:
                try:
                    new_message = get_message(service, user_id, message['id'], labels)
                    if new_message is not None:
                        result.append(new_message)
                except KeyError:
                    print "Failed:"
                    print message
        except SSLError, e:
            print "Thread Timeout"
            print thread
            print e.__class__
            print e
            traceback.print_exc()

    return result

    # except HttpError, e:
    #     print_error('Error: Failed to retrieve thread messages for: ' + str(user_id) + ' using query: ' + str(query))
    #     raise e
