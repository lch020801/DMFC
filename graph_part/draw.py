import matplotlib.pyplot as plt
import numpy as np

matrix = np.random.rand(4,4)

plt.imshow(matrix, cmap='YlGnBu')
plt.colorbar()
plt.show()