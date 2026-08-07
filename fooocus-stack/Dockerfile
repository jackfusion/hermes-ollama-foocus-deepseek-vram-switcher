FROM nvcr.io/nvidia/pytorch:26.06-py3
WORKDIR /opt/Fooocus
RUN git clone https://github.com/mrhan1993/Fooocus-API.git .
RUN unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY && pip install -r requirements.txt && pip uninstall -y opencv-python opencv-python-headless opencv-contrib-python && rm -rf /usr/local/lib/python3.12/dist-packages/cv2 /usr/local/lib/python3.12/dist-packages/opencv* /usr/lib/python3/dist-packages/cv2 /usr/lib/python3/dist-packages/opencv* && pip install opencv-python-headless==4.7.0.72
CMD ["python", "main.py", "--host", "0.0.0.0", "--port", "7865"]
