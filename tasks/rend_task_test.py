import numpy as np
from PIL import Image
from rend_task import RenderTask

def test_render_task():
    print("Starting RenderTask test...")

    task1 = RenderTask(width=250, height=250) 

    result = task1.execute()

    image_data = np.frombuffer(result['image_data'], dtype=np.uint8).reshape(result['shape'])

    img = Image.fromarray(image_data, 'RGB')
    img.save("render_test_output.png")

    print("done, look for render_test_output.png")
    print(f"Image size: {result['shape']}")


if __name__ == "__main__":
    test_render_task()