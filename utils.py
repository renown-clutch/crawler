from pyzbar.pyzbar import decode
from PIL import Image
import numpy as np

def extract_qr_matrix_from_divs(divs_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(divs_html, "html.parser")
    divs = soup.find_all("div")

    size = int(len(divs) ** 0.5)
    
    matrix = []

    for i, div in enumerate(divs):
        style = div.get("style")
        is_white = "white" in style
        pixel = 0 if not is_white else 255
        if i % size == 0:
            matrix.append([])
        matrix[-1].append(pixel)

    return matrix

def decode_qr_from_matrix(matrix):
    array = np.array(matrix, dtype=np.uint8)
    image = Image.fromarray(array, mode='L')
    image = image.resize((array.shape[1] * 10, array.shape[0] * 10), Image.NEAREST)

    result = decode(image)
    if result:
        return result[0].data.decode()
    return None
