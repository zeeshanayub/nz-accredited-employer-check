import json

import requests


def check_employer_accredited(query, collection='2', page='1'):
    """Query the NZ Immigration Accredited Employer list API for `query`.

    Returns the parsed response dict. A match has a `results` list; no match
    returns {"Title": "No Results", ...} instead (no `results` key).
    """
    files = {
        'query': (None, query),
        'collection': (None, collection),
        'page': (None, page),
    }

    response = requests.post(
        'https://www.immigration.govt.nz/list-api/getAPIResults/',
        files=files
    )

    try:
        data = response.json()
    except ValueError:
        data = {'raw_response': response.text}

    if isinstance(data, dict) and isinstance(data.get('results'), str):
        try:
            data['results'] = json.loads(data['results'])
        except json.JSONDecodeError:
            pass

    return data


if __name__ == '__main__':
    data = check_employer_accredited('RWA People')
    print(json.dumps(data, indent=2, ensure_ascii=False))