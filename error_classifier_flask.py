from flask import Flask, request, jsonify
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image
import numpy as np
from tensorflow.keras.layers import Layer
import tensorflow as tf

error_classifier_model = load_model('/Users/rajatchadha/Desktop/Optimus/image_classifier_model_final.h5')
classifier_indices = {'captcha_issue': 0, 'catalog_issue': 1, 'dependency_issue': 2, 'dirty_booking_issue': 3, 'no_idea_issue': 4, 'portal_issue': 5, 'post_payment_issue': 6, 'proxy_issue': 7,'selenium_issue': 8}

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

print(classify_image('/Users/rajatchadha/Desktop/Optimus/portal_issue/failure_18381095_14476798_D5772915B0B44105B7324147F13BC689_ParkGuell.jpeg'))