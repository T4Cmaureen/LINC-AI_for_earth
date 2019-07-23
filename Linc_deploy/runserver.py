# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# # /ai4e_api_tools has been added to the PYTHONPATH, so we can reference those
# libraries directly.
import json
from flask import Flask, request, abort
from ai4e_app_insights_wrapper import AI4EAppInsights
from ai4e_service import APIService
from os import getenv
from datetime import datetime

# import our base code
from predict_AI.py import LINC_detector

print('Creating Application')

app = Flask(__name__)

# Use the AI4EAppInsights library to send log messages. NOT REQURIED
log = AI4EAppInsights()

# Use the APIService to execute your functions within a logging trace, which supports long-running/async functions,
# handles SIGTERM signals from AKS, etc., and handles concurrent requests.
with app.app_context():
    ai4e_service = APIService(app, log)

# Load LINC model
LINC = LINC_detector(getenv('MODEL_PATH'))

# Define a function for processing request data, if appliciable.  This function loads data or files into
# a dictionary for access in your API function.  We pass this function as a parameter to your API setup.
#return_values passed to **kwargs
def process_request_data(request):
    return_values = {'detection_confidence': float(getenv('DEFAULT_DETECTION_CONFIDENCE'))}
    try:
        # Attempt to load the files
        images, image_names = [], []
        for k, file in request.files.items():
            print(file.content_type)
            # file of type SpooledTemporaryFile has attributes content_type and a read() method
            images.append(file)
            image_names.append(k)
            
        return_values['images'] = images
        return_values['image_names'] = image_names
        args = request.args
    except:
        log.log_error('Unable to load the request data')   # Log to Application Insights

    if len(images) > int(getenv('MAX_IMAGES_ACCEPTED')):
        abort(413, 'Too many images. Maximum number of images that can be processed in one call is {}.'.format(str(getenv('MAX_IMAGES_ACCEPTED'))))

    if 'confidence' in args:
        detection_confidence = float(args['confidence'])
        print('runserver, post_detect_sync, detection confidence: ', detection_confidence)
        if detection_confidence < 0.0 or detection_confidence > 1.0:
            abort(400, 'Detection confidence {} is invalid. Needs to be between 0.0 and 1.0.'.format(detection_confidence))
        else:
            return_values['detection_confidence'] = detection_confidence

    return return_values

# POST, long-running/async API endpoint example
@ai4e_service.api_async_func(
    api_path = '/detect', 
    methods = ['POST'], 
    request_processing_function = process_request_data, # This is the data process function that you created above.
    maximum_concurrent_requests = 5, # If the number of requests exceed this limit, a 503 is returned to the caller.
    content_types = ['image/png', 'application/octet-stream', 'image/jpeg'],
    content_max_length = (5 * 8) * 1000000,  # 5MB per image * number of images allowed
    trace_name = 'post:post_detect_async')
def default_post(*args, **kwargs):
    # Since this is an async function, we need to keep the task updated.
    taskId = kwargs.get('taskId')
    log.log_debug('Started task', taskId) # Log to Application Insights
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'running - default_post')

    # Get the data from the dictionary key that you assigned in your process_request_data function.
    detection_confidence = kwargs.get('detection_confidence')
    images = kwargs.get('images')
    image_names = kwargs.get('image_names')

    try:
        print('runserver, post_detect_sync, batching and inferencing...')
        ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'running - batching and inferencing')
        # detections is an array of dicts
        tic = datetime.now()

        result = LINC.detect(images, image_names, detection_confidence)

        #detections = generate_detections_batch(images)
        toc = datetime.now()
        inference_duration = toc - tic
        print('runserver, post_detect_sync, inference duration: {} seconds.'.format(inference_duration))
    except Exception as e:
        print('Error performing detection on the images: ' + str(e))
        log.log_exception('Error performing detection on the images: ' + str(e))
        ai4e_service.api_task_manager.FailTask(taskId, 'Task failed - ' + 'Error performing detection on the images: ' + str(e))
        return -1
    """#NOT NEEDED NOW
    # filter the detections by the confidence threshold
    ai4e_service.api_task_manager.UpdateTaskStatus(taskId, 'returning - filtering')
    try:
    except Exception as e:
        print('Error consolidating the detection boxes: ' + str(e))
        log.log_exception('Error consolidating the detection boxes: ' + str(e))
        ai4e_service.api_task_manager.FailTask('Error consolidating the detection boxes: ' + str(e))
    """
    # Once complete, ensure the status is updated.
    log.log_debug('Completed task', taskId) # Log to Application Insights
    # Update the task with a completion event.
    ai4e_service.api_task_manager.CompleteTask(taskId, 'completed - ' + json.dumps(result))

# GET, sync API endpoint example
@ai4e_service.api_sync_func(api_path = '/model_version', methods = ['GET'], maximum_concurrent_requests = 1000, trace_name = 'get:get_model_version')
def get_model_version(*args, **kwargs):
    try:
        return getenv('MODEL_VERSION')
    except:
        return 'Model version unknown.'

if __name__ == '__main__':
    app.run()