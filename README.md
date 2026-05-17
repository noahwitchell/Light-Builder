Light Builder for Blender
Author: NBW
Version: 1.0
Blender Compatibility: 4.0+

Light Builder is an interactive lighting suite designed to drastically speed up scene illumination. It allows the user to enter a placement mode and click any surface in the viewport and instantly populate it with lights.

There are five modes currently:

Uplight Mode Quickly places lights aligned to surface normals facing upward to simulate floor or ground lighting. You can click to place individual lights or use Shift + Click twice to draw an array of lights along the line between those two points, using the arrow keys to add or subtract the number of lights along the line.

Downlight Mode Functions identically to Uplight mode but aligns the lights to face downward from your target surface, perfect for ceiling lighting. You can instantly toggle back and forth between Uplight and Downlight modes on the fly by pressing TAB.

Targeted Mode Built for precise area lighting, this mode anchors a light to your clicked location and lets you interactively orbit the light around that exact point. Moving your cursor dynamically adjusts the rotation angle until you click or hit enter to commit the angle, allowing you to dynamically adjust the distance from the target in real-time before you click or hit enter to lock it in.

Aimed Mode drops a spotlight either on a clicked surface or automatically at the deepest back boundary of your scene's bounding box. You can then dynamically aim the spot directly at your cursor until you click or hit enter to commit the angle, then adjust its distance along that aiming vector before confirming the placement with another click.

Isolated Rim Light Rig is unavailable in placement mode as it relies on selecting objects you would like to rim light. Once selected, hitting the Isolated Rim Light Rig button will add an area light to your scene that is scaled and positioned according to the bounding box of the selected objects, and is light linked to those object so that they are the only objects receiving light from the newly added rim light.

There's also a symmetry mode that when enabled will make a live copy of the lights you add and manipulate on the opposite side of the X axis, as you add them. This is great for saving time lighting in scenes that are symmetrical.

There are also some parameters that can be adjusted such as creating new stored defaults for the different light types as well as changing the linking behavior when adding new lights.
