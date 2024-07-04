import slack
import os
import json
import csv
from PIL import Image
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, Response, jsonify
from slackeventsapi import SlackEventAdapter
import requests
from datetime import datetime, timedelta,timezone
from PIL import Image, UnidentifiedImageError
from slack.errors import SlackApiError
from concurrent.futures import ThreadPoolExecutor
import json
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image
import numpy as np
from tensorflow.keras.layers import Layer
import tensorflow as tf

error_classifier_model = load_model('/Users/rajatchadha/Desktop/Optimus/image_classifier_model_final.h5')
classifier_indices = {'captcha_issue': 0, 'catalog_issue': 1, 'dependency_issue': 2, 'dirty_booking_issue': 3, 'no_idea_issue': 4, 'portal_issue': 5, 'post_payment_issue': 6, 'proxy_issue': 7,'selenium_issue': 8}

env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

app = Flask(__name__)
slack_event_adapter = SlackEventAdapter(os.environ['SIGNING_SECRET'], '/slack/events', app)

client = slack.WebClient(token=os.environ['SLACK_TOKEN'])
BOT_ID = client.api_call("auth.test")["user_id"]

executor = ThreadPoolExecutor(max_workers=4)

def classify_image(img_path):
    img = image.load_img(img_path, target_size=(215, 215))
    img_array = image.img_to_array(img) / 255.0
    img_array = np.expand_dims(img_array, axis=0)

    prediction = error_classifier_model.predict(img_array)
    # print(prediction)
    class_idx = np.argmax(prediction)
    class_label = classifier_indices
    class_label = {v: k for k, v in class_label.items()}

    return class_label[class_idx]


def download_image(file_id, filename):
    try:
        file_info = client.files_info(file=file_id)
        image_url = file_info['file']['url_private_download']
        headers = {"Authorization": f"Bearer {os.environ['SLACK_TOKEN']}"}
        
        response = requests.get(image_url, headers=headers, stream=True)
        if response.status_code == 200:
            with open("ss/"+filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            # Verify the downloaded image
            try:
                with Image.open("ss/"+filename) as img:
                    img.verify()  # Verify if it is a valid image
            except UnidentifiedImageError:
                print(f"Downloaded file {filename} is not a valid image.")
                os.remove(filename)
                return False
            return True
        else:
            print(f"Failed to download {filename} - Status code: {response.status_code}")
    except SlackApiError as e:
        print(f"Failed to download image: {e.response['error']}")
    return False

def convert_image_to_jpeg(filename):
    try:
        with Image.open("ss/"+filename) as img:
            jpeg_filename = "ss/"+filename.rsplit('.', 1)[0] + ".jpeg"
            img.convert("RGB").save(jpeg_filename, "JPEG")
        os.remove("ss/"+filename)  # Remove the original file
        return jpeg_filename
    except UnidentifiedImageError:
        print(f"Failed to identify and convert image: ss/{filename}")
        os.remove("ss/"+filename)
        return None

def parse_filename(filename):
    parts = filename.split('_')
    if len(parts) >= 5:
        booking_id = parts[1]
        itinerary_id = parts[2]
        trace_token = parts[3]
        return booking_id, itinerary_id, trace_token
    return None, None, None

def fetch_all_messages(channel_id, time_from):
    all_messages = []
    cursor = None
    cnt=0
    while True:
        try:
            if cursor:
                try:
                    response = client.conversations_history(channel=channel_id, oldest=time_from, cursor=cursor)
                except Exception as ex:
                    print(str(ex))
                    response = client.conversations_history(channel=channel_id, oldest=time_from, cursor=cursor)
            else:
                try:
                    response = client.conversations_history(channel=channel_id, oldest=time_from)
                except Exception as ex:
                    print(str(ex))
                    response = client.conversations_history(channel=channel_id, oldest=time_from)
            cnt+=1
            print(cnt)
            messages = response['messages']
            all_messages.extend(messages)
            
            if not response.get('has_more'):
                break
            
            cursor = response.get('response_metadata', {}).get('next_cursor')
        
        except SlackApiError as ex:
            
            print(f"Error fetching conversations: {str(ex)}")
            break

    return all_messages

def error_message_to_next_best_action_mapping(error_message, data, coralogix_log):
    coralogix_log = str(coralogix_log)
    x = {
        "catalog_issue" : f"<!subteam^S05V9HPTR41> Please check the booking id -> " + str(data['booking_id']) + " something seems wrong with the suppliers config.",
        "dependency_issue" : f"<!subteam^S03R72MC76F> please check the booking id -> " + str(data['booking_id']) + ", dependency on customer inputs/something which needs to coordinated with cx",
        "dirty_booking_issue" : f"<!subteam^S03R72MC76F> please check the booking id -> " + str(data['booking_id']) + ", please send alts here to avoid UF",
        "portal_issue" : f"<!subteam^S05877C8S6A> <S03R72MC76F> please check the booking id -> " + str(data['booking_id']) + ", seems to be a portal issue.In case the portal is not reachable please close the urgent or switch the vendor priority. Coralogix Log -> " + coralogix_log,
        "post_payment_issue" :  f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", seems to be a post-payment issue. Coralogix Log -> " + coralogix_log,
        "proxy_issue" : f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", seems to be a proxy issue. Coralogix Log -> " + coralogix_log,
        "selenium_issue" : f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", seems to be a selenium issue. Coralogix Log -> " + coralogix_log,
        "no_idea_issue" : f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", cannot find the correct issue. Coralogix Log -> " + coralogix_log,
        "captcha_issue" : f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", cannot solve the captcha. Coralogix Log -> " + coralogix_log,
        "driver_creation_issue" : f"<!subteam^S05877C8S6A> please check the booking id -> " + str(data['booking_id']) + ", failed to create the driver. Coralogix Log -> " + coralogix_log
    }
    return x[error_message]


def get_trace_token(traceToken):
    try:
        apiKey = 'cxtp_hUziRhXPpk45elUCd2YpQORcOUFpEn'
        url = 'https://ng-api-http.coralogix.com/api/v1/dataprime/query'

        traceToken = traceToken
        filter_expression = f"source logs | filter text.contains('{traceToken}') | limit 1"

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)

        endDate = now.isoformat().replace('+00:00', 'Z')
        startDate = start.isoformat().replace('+00:00', 'Z')

        query = {
            "severity": [4, 5],
            "query": filter_expression,
            "metadata": {
                "startDate": startDate,
                "endDate": endDate,
            }
        }

        headers = {
            'Authorization': 'Bearer ' + apiKey,
            'Content-Type': 'application/json'
        }

        response = requests.post(url, headers=headers, data=json.dumps(query))

        # Check if the request was successful
        if response.status_code == 200:
            logs = response.json()['result']['results'][0]['metadata']
            for log in logs:
                if log['key']=='timestamp':
                    timestamp=log['value']
                if log['key']=='logid':
                    log_id = log['value']
            if logs:
                # Extract log information (example: taking the first log)

                # Construct permalink (adjust based on your Coralogix account settings)
                permalink = f"https://headout.coralogix.com/#/query-new/archive-logs?permalink=true&logId={log_id}"

                return permalink
            else:
                print("No logs found for the given query.")
        else:
            print(f"Error: {response.status_code} - {response.text}")
    except Exception as ex:
        pass

def download_images_from_channel(channel_id, time_from):
    try:
        messages = fetch_all_messages(channel_id, time_from)
        # with open('my_object.json', 'r') as json_file:
        #     messages = json.load(json_file)
        with open('my_object.json', 'w') as file:
            json.dump(messages, file)
        csv_data = []
        
        for message in messages:
            if "files" in message:
                for file in message["files"]:
                    if file["mimetype"].startswith("image/"):
                        image_url = file["url_private"]
                        original_filename = file["name"]
                        print(original_filename)
                        if len(original_filename.split('_')) == 5 and len(original_filename.split('_')[1]) == 8 and original_filename.split('_')[0] == 'failure' and ('ParkGuell' in original_filename.split('_')[4] or 'Acropolis' in original_filename.split('_')[4] or 'Alcazar' in original_filename.split('_')[4] or 'Seville Cathedral Official' in original_filename.split('_')[4] or 'BudapestSpa' in original_filename.split('_')[4]):
                            file_id = file["id"]
                            headers = {"Authorization": f"Bearer {os.environ['SLACK_TOKEN']}"}
                            
                            if download_image(file_id, original_filename):
                                jpeg_filename = convert_image_to_jpeg(original_filename)
                                if jpeg_filename:
                                    booking_id, itinerary_id, trace_token = parse_filename(original_filename)
                                    csv_data.append([jpeg_filename, booking_id, itinerary_id, trace_token])
        
        with open('images_data.csv', 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(["image_path", "booking_id", "itinerary_id", "trace_token"])
            csv_writer.writerows(csv_data)
        final_data = []
        for data in csv_data:
            final_data.append({"error_message":classify_image(data[0]), "booking_id": data[1], "trace_token": data[3]})

        for data in final_data:
            coralogix_log = get_trace_token(data["trace_token"])
            error_message = error_message_to_next_best_action_mapping(data['error_message'], data, coralogix_log)
            response = client.chat_postMessage(
            channel=channel_id,
            text= error_message
        )
        
        print("CSV file created successfully.")
    
    except SlackApiError as e:
        print(f"Error fetching conversations: {e.response['error']}")


@app.route('/any-sus', methods=["POST"])
def create_and_deploy_crons():
    trigger_id = request.form.get('trigger_id')
    channel_id = request.form.get('channel_id')
    one_hour_ago = datetime.now() - timedelta(hours=1)
    one_hour_ago_timestamp = one_hour_ago.timestamp()
    download_images_from_channel(channel_id, one_hour_ago_timestamp)
    # modal_view = {
    #     "type": "modal",
    #     "callback_id": "create_cron_modal",
    #     "private_metadata": channel_id,
    #     "title": {
    #         "type": "plain_text",
    #         "text": "Create Cron Job"
    #     },
    #     "blocks": [
    #         {
    #             "type": "input",
    #             "block_id": "dag_id",
    #             "element": {
    #                 "type": "plain_text_input",
    #                 "action_id": "input1",
    #                 "placeholder": {
    #                     "type": "plain_text",
    #                     "text": "For eg:  vendor.inventory.test-0-5"
    #                 }
    #             },
    #             "label": {
    #                 "type": "plain_text",
    #                 "text": "Enter Dag Name",
    #                 "emoji": True
    #             }
    #         },
    #         {
    #             "type": "input",
    #             "block_id": "cron_frequency",
    #             "element": {
    #                 "type": "plain_text_input",
    #                 "action_id": "input2",
    #                 "placeholder": {
    #                     "type": "plain_text",
    #                     "text": "For eg:  */15 * * * * "
    #                 }
    #             },
    #             "label": {
    #                 "type": "plain_text",
    #                 "text": "Enter Cron Frequency [Reference: https://crontab.guru/ ]",
    #                 "emoji": True
    #             }
    #         },
    #         {
    #             "type": "input",
    #             "block_id": "target_function",
    #             "element": {
    #                 "type": "plain_text_input",
    #                 "action_id": "input3",
    #                 "placeholder": {
    #                     "type": "plain_text",
    #                     "text": "For eg:  selenium.inventory.test-0-5"
    #                 }
    #             },
    #             "label": {
    #                 "type": "plain_text",
    #                 "text": "Enter Cron Target Function",
    #                 "emoji": True
    #             }
    #         },
    #         {
    #             "type": "input",
    #             "block_id": "cron_schedule",
    #             "element": {
    #                 "type": "plain_text_input",
    #                 "action_id": "input4",
    #                 "placeholder": {
    #                     "type": "plain_text",
    #                     "text": """For eg:  {"offset_days":0,"num_days":1}"""
    #                 }
    #             },
    #             "label": {
    #                 "type": "plain_text",
    #                 "text": "Enter Cron Schedule",
    #                 "emoji": True
    #             }
    #         },
    #         {
    #             "type": "input",
    #             "block_id": "cron_type",
    #             "element": {
    #                 "type": "static_select",
    #                 "action_id": "dropdown",
    #                 "placeholder": {
    #                     "type": "plain_text",
    #                     "text": "Select an option"
    #                 },
    #                 "options": [
    #                     {
    #                         "text": {
    #                             "type": "plain_text",
    #                             "text": "Selenium Inv"
    #                         },
    #                         "value": "selenium"
    #                     },
    #                     {
    #                         "text": {
    #                             "type": "plain_text",
    #                             "text": "Selenium Inv | Chrome"
    #                         },
    #                         "value": "selenium_chrome"
    #                     }
    #                 ]
    #             },
    #             "label": {
    #                 "type": "plain_text",
    #                 "text": "Select Cron Type",
    #                 "emoji": True
    #             }
    #         }
    #     ],
    #     "submit": {
    #         "type": "plain_text",
    #         "text": "Submit"
    #     }
    # }
    
    # # Open the modal
    # client.views_open(
    #     trigger_id=trigger_id,
    #     view=modal_view
    # )
    return Response(), 200

@app.route('/slack/interactivity', methods=['POST'])
def handle_interactive_message():
    payload = json.loads(request.form.get('payload'))
        
    if payload['type'] == 'view_submission':
        view = payload['view']
        state_values = view['state']['values']        
        inputs = {
            'dag_id': state_values['dag_id']['input1']['value'],
            'cron_frequency': state_values['cron_frequency']['input2']['value'],
            'target_function': state_values['target_function']['input3']['value'],
            'cron_schedule': state_values['cron_schedule']['input4']['value'],
            'cron_type': state_values['cron_type']['dropdown']['selected_option']['value'],
            'user_id': payload['user']['id'],
            'channel_id': payload['view']['private_metadata']
        }
        client.views_update(
            view_id=payload['view']['id'],
            view={
                "type": "modal",
                "callback_id": "create_cron_modal",
                "title": {
                    "type": "plain_text",
                    "text": "Create Cron Job"
                },
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Processing your request..."
                        }
                    }
                ]
            }
        )
        # Trigger the /create-dag route with the collected data
        executor.submit(create_dag, inputs)
    return Response(), 200

def create_dag(inputs):
    user_id = inputs['user_id']
    channel_id = inputs['channel_id']
    url = "https://ergo.headout.com/dagen/dags/create"
    payload = {'template_id': 'ErgoChronosTemplate',
    'csrf_token': 'IjgzZWQxYTA5ZTFjMmNkMDcwMDMyNTdjZTQxNDAyNTZmNTJiNjQ5ZGMi.ZnutsA.NazMI7Ff7ZjTJAQ9fJEWhQusMsc',
    'dag_id': inputs['dag_id'],
    'schedule_interval': inputs['cron_frequency'],
    'start_date': '2020-09-01 00:00:00',
    'category': inputs['cron_type'],
    'ergo_task_preset': '_new_',
    'ergo_task_id': inputs['target_function'],
    'ergo_task_data': inputs['cron_schedule']}
    files=[

    ]
    headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'en-IN,en-GB;q=0.9,en;q=0.8,hi-IN;q=0.7,hi;q=0.6,en-US;q=0.5',
    'cache-control': 'max-age=0',
    'cookie': '_gcl_au=1.1.1431567491.1712761153; _fbp=fb.1.1712761154753.1231563802; h-sid=42c3b0c4-9866-4ef6-aae4-0e4cb58a2341; __ssid=f677f03f34a27b7047e3a3ea34a8806; h-attr=%5B%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1712788013667%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2Fselect%2F%22%2C%22ts%22%3A1712788116488%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fconfirmation%2FVmA-67dyWtMLvCsVu2KyPWuUumpJm494aMEsU3pJmdCeb2Z0K6Ay0FYLo6McpvBWYSteNqrsw_w6J7ONRlWvkA%3D%3D%2F%22%2C%22ts%22%3A1712816198772%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1712897795651%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fredirect%2Fe-2545%253C%2F%22%2C%22ts%22%3A1713007137759%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fburj-khalifa-tickets%2Fcombo-burj-khalifa-at-the-top-dubai-aquarium-ticket-e-2545%2F%22%2C%22ts%22%3A1713007147551%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F27211%2F%22%2C%22ts%22%3A1713010329026%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F9651%2F%22%2C%22ts%22%3A1713010510893%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F23927%2F%22%2C%22ts%22%3A1713123788467%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1713338942157%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fcare%2F%22%2C%22ts%22%3A1713338948574%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fredirect%2Fe-25017%253C%2F%22%2C%22ts%22%3A1713373706040%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fwieliczka-salt-mine-tickets%2Fguided-tour-of-wieliczka-salt-mines-miners-route-with-skip-the-line-entry-e-25017%2F%22%2C%22ts%22%3A1713373710368%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1713413412904%7D%5D; content_lang=en; googtrans=%2Fen%2Fen; _clck=1qddsx7%7C2%7Cfmt%7C0%7C1561; _uetvid=e505f9b0f74a11eebaa961d338a5efe6; _iidt=zK4aeCT2uaMWmdKUamuJ9xYstXts+cLIuX8uoU5bfU2W5OotPGQZxtzz8eunKGt+U2TY0QXhgbaJzTZp+R5UPvlMcWULtjyEMpCpcFQ=; _vid_t=SbGbOFUJY23DVregB7nalS8SFG81jrFJIfG4lZ4Wh67sI+8U+R6ryu3N7rp9FdjRmg7T8sOcrU1+LQW6WK63WcvIYWPercmwHeZw4lY=; ory_kratos_continuity=MTcxOTA4OTc3OXxEWDhFQVFMX2dBQUJFQUVRQUFCZl80QUFBUVp6ZEhKcGJtY01Jd0FoYjNKNVgydHlZWFJ2YzE5dmFXUmpYMkYxZEdoZlkyOWtaVjl6WlhOemFXOXVCbk4wY21sdVp3d21BQ1F4WkRjMFpXUmpOeTFtWVRFekxUUXdNalF0T0dNellpMWlPRFl3WXpBNU9Ua3dOV009fPw41-WaJIj3SBe6REKYuXr4JLf1sPn7_PjvV2VK3m3t; csrf_token_910c9cdb2c157b286c33e102a6e3cf6b5c3bf2760ad1090c4703f63a43dc35c9=XW8T1nHUtc6ukCy0FXl/dDYig5lKkuK/y44r4mTo8tc=; ory_session_angryhertzf78ol5nls8=MTcxOTA4OTc4NHxkOHUwVl95RmxhOWNNVlF0YUtXZ2ZiN24tSEFGOFg0NU94ODBPUU9wNmJBYmpJVW4zR1oxcmxkTDhwbU9QRERrMkJhRm1BN19SVFozVXlWcF8zdUl3bF9HdjZPNG1OYVFDNjdSdzE1eElfeHhfU1QtUEF2akVFTVEtUkMtZDFJUE54X2Z5QkdYVWJFSmtOWjVveV9WVmtobHZ4RXVHa1kxaGNHNGJlWUFyS2MtcWlGcXRGRFV5T0FoTzBfZUl4U25rYzU4VDZ4a0JkemxybFdSYTV0eWNUS0loNGhDQ2xMRWljT1JoMDZIMnZGVDZqekRDaDU2TjNmZ3VVNU4ybndRc0M0c3JKWE9JZnZkbVZVM2tISmV8oI3BXmZd64Ec860KoiRskUR0DWLn7bda1Vz8wTSAQUM=; _ga=GA1.2.789864506.1712761154; _ga_FV04TTE58T=GS1.2.1719090628.21.1.1719090657.0.0.0; _ga_Y45PC9R73C=GS1.1.1719258646.107.0.1719258646.60.0.0; session=.eJx1kcGO3CAMht-FQ08zEyAEQqRVH6SqkIPNJFqGjIDsqqr67vW0vfaCbeH_45P4KUKq1DaxJMiNLiLsKBYRxwQzzWq2BhNqi8o5havhSivQ5FYlaQYnjUfl0UzO806yQErbUYKNFn2yJqo5meghutGsI8clMnnUWpkVp1kl8MpxHJOWTE6e0K9x8oJFnlQfUKh0sfR6slpsNYV-vFNhw3kkVCA9qagjSiflqCcXySgj9WTTpFfLcpFJCPfQOvSzhbTnTpXjkDPf5CNCJh4ZeRFPuFPY9taP-kMs38TW-3MZBqr347YR4HH2WzweA_PqWYbMm8PXkHIPY3i9seNbo0xlPx-3Dyp41NteuHnxrtdCZ4vbJ5TWaS_hCRkiXbW8qunLP8hLkt7aGSO1xkL_Ezgb1fbn3Es6BvH9Il7D369T4tdvICeYog.Znutqw.S7-zVFTWHTO5BDl2TjjCDjnkgTM',
    'dnt': '1',
    'origin': 'https://ergo.headout.com',
    'priority': 'u=0, i',
    'referer': 'https://ergo.headout.com/dagen/dags/create',
    'sec-ch-ua': '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
    }

    response = requests.request("POST", url, headers=headers, data=payload, files=files)
    url = f"https://ergo.headout.com/dagen/dags/approve?dag_id=selenium.{inputs['dag_id']}"
    payload = {}
    headers = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'accept-language': 'en-IN,en-GB;q=0.9,en;q=0.8,hi-IN;q=0.7,hi;q=0.6,en-US;q=0.5',
    'cookie': '_gcl_au=1.1.1431567491.1712761153; _fbp=fb.1.1712761154753.1231563802; h-sid=42c3b0c4-9866-4ef6-aae4-0e4cb58a2341; __ssid=f677f03f34a27b7047e3a3ea34a8806; h-attr=%5B%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1712788013667%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2Fselect%2F%22%2C%22ts%22%3A1712788116488%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fconfirmation%2FVmA-67dyWtMLvCsVu2KyPWuUumpJm494aMEsU3pJmdCeb2Z0K6Ay0FYLo6McpvBWYSteNqrsw_w6J7ONRlWvkA%3D%3D%2F%22%2C%22ts%22%3A1712816198772%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1712897795651%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fredirect%2Fe-2545%253C%2F%22%2C%22ts%22%3A1713007137759%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fburj-khalifa-tickets%2Fcombo-burj-khalifa-at-the-top-dubai-aquarium-ticket-e-2545%2F%22%2C%22ts%22%3A1713007147551%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F27211%2F%22%2C%22ts%22%3A1713010329026%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F9651%2F%22%2C%22ts%22%3A1713010510893%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F23927%2F%22%2C%22ts%22%3A1713123788467%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1713338942157%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fcare%2F%22%2C%22ts%22%3A1713338948574%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fredirect%2Fe-25017%253C%2F%22%2C%22ts%22%3A1713373706040%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fwieliczka-salt-mine-tickets%2Fguided-tour-of-wieliczka-salt-mines-miners-route-with-skip-the-line-entry-e-25017%2F%22%2C%22ts%22%3A1713373710368%7D%2C%7B%22lp%22%3A%22https%3A%2F%2Fwww.headout.com%2Fbook%2F2819%2F%22%2C%22ts%22%3A1713413412904%7D%5D; content_lang=en; googtrans=%2Fen%2Fen; _clck=1qddsx7%7C2%7Cfmt%7C0%7C1561; _uetvid=e505f9b0f74a11eebaa961d338a5efe6; _iidt=zK4aeCT2uaMWmdKUamuJ9xYstXts+cLIuX8uoU5bfU2W5OotPGQZxtzz8eunKGt+U2TY0QXhgbaJzTZp+R5UPvlMcWULtjyEMpCpcFQ=; _vid_t=SbGbOFUJY23DVregB7nalS8SFG81jrFJIfG4lZ4Wh67sI+8U+R6ryu3N7rp9FdjRmg7T8sOcrU1+LQW6WK63WcvIYWPercmwHeZw4lY=; ory_kratos_continuity=MTcxOTA4OTc3OXxEWDhFQVFMX2dBQUJFQUVRQUFCZl80QUFBUVp6ZEhKcGJtY01Jd0FoYjNKNVgydHlZWFJ2YzE5dmFXUmpYMkYxZEdoZlkyOWtaVjl6WlhOemFXOXVCbk4wY21sdVp3d21BQ1F4WkRjMFpXUmpOeTFtWVRFekxUUXdNalF0T0dNellpMWlPRFl3WXpBNU9Ua3dOV009fPw41-WaJIj3SBe6REKYuXr4JLf1sPn7_PjvV2VK3m3t; csrf_token_910c9cdb2c157b286c33e102a6e3cf6b5c3bf2760ad1090c4703f63a43dc35c9=XW8T1nHUtc6ukCy0FXl/dDYig5lKkuK/y44r4mTo8tc=; ory_session_angryhertzf78ol5nls8=MTcxOTA4OTc4NHxkOHUwVl95RmxhOWNNVlF0YUtXZ2ZiN24tSEFGOFg0NU94ODBPUU9wNmJBYmpJVW4zR1oxcmxkTDhwbU9QRERrMkJhRm1BN19SVFozVXlWcF8zdUl3bF9HdjZPNG1OYVFDNjdSdzE1eElfeHhfU1QtUEF2akVFTVEtUkMtZDFJUE54X2Z5QkdYVWJFSmtOWjVveV9WVmtobHZ4RXVHa1kxaGNHNGJlWUFyS2MtcWlGcXRGRFV5T0FoTzBfZUl4U25rYzU4VDZ4a0JkemxybFdSYTV0eWNUS0loNGhDQ2xMRWljT1JoMDZIMnZGVDZqekRDaDU2TjNmZ3VVNU4ybndRc0M0c3JKWE9JZnZkbVZVM2tISmV8oI3BXmZd64Ec860KoiRskUR0DWLn7bda1Vz8wTSAQUM=; _ga=GA1.2.789864506.1712761154; _ga_FV04TTE58T=GS1.2.1719090628.21.1.1719090657.0.0.0; _ga_Y45PC9R73C=GS1.1.1719258646.107.0.1719258646.60.0.0; session=.eJx1kcGO3CAMht-FQ08zEyAEQqRVH6SqkIPNJFqGjIDsqqr67vW0vfaCbeH_45P4KUKq1DaxJMiNLiLsKBYRxwQzzWq2BhNqi8o5havhSivQ5FYlaQYnjUfl0UzO806yQErbUYKNFn2yJqo5meghutGsI8clMnnUWpkVp1kl8MpxHJOWTE6e0K9x8oJFnlQfUKh0sfR6slpsNYV-vFNhw3kkVCA9qagjSiflqCcXySgj9WTTpFfLcpFJCPfQOvSzhbTnTpXjkDPf5CNCJh4ZeRFPuFPY9taP-kMs38TW-3MZBqr347YR4HH2WzweA_PqWYbMm8PXkHIPY3i9seNbo0xlPx-3Dyp41NteuHnxrtdCZ4vbJ5TWaS_hCRkiXbW8qunLP8hLkt7aGSO1xkL_Ezgb1fbn3Es6BvH9Il7D369T4tdvICeYog.Znutqw.S7-zVFTWHTO5BDl2TjjCDjnkgTM',
    'dnt': '1',
    'priority': 'u=0, i',
    'referer': 'https://ergo.headout.com/dagen/dags',
    'sec-ch-ua': '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'same-origin',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
    'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
    }
    response = requests.request("GET", url, headers=headers, data=payload)
    print(response.text)
    if response.status_code == 200:
        # Send success message to the Slack channel
        client.chat_postMessage(
            channel=channel_id,
            text=f"<@{user_id}> has successfully created the cron job. [View the cron job](https://ergo.headout.com/tree?dag_id=selenium.{inputs['dag_id']})"  # Replace with actual link
        )
    else:
        # Send failure message to the Slack channel
        client.chat_postMessage(
            channel=channel_id,
            text=f"Failed to create the cron job with Dag ID: selenium.{inputs['dag_id']} for <@{user_id}>."
        )    

if __name__ == "__main__":
    app.run(debug=True, port=3000)
