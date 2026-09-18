Light Builder for Blender
Author: NBW
Version: 1.1
Blender Compatibility: 4.0+

New version of Light Builder (1.10) with the following fixes and features:

Fixes:

-The light placement mode is now consistently showing context awareness when hovering over an N-Panel window vs hovering over the viewport. It knows if you are trying to place a light vs access a control panel.
-Light placement now ignores objects that have camera ray visibility disabled.
-Shift/Tilda navigation mode works in the sense that it doesn't break navigation or cancel light placement mode, but there's no way to keep the cursor tracking aspect of the navigation live while also placing lights. You have to commit the navigation change with a click before placing additional lights with further clicking.
-There is a Hotkeys twirldown in the control panel for checking what buttons do what, plus the HUD is more prominent in the bottom left corner of the viewport so you can see what light mode is active as well as hotkeys to use to switch modes.
-Canceling a light placement before commiting and deleting lights now both happen by pressing X. This is different than previously where the escape key was used to cancel a light placement in progress and X didn't work in light placement mode. Now Esc is only used to exit light placement mode and X manages canceling and deleting lights.
-Light placement mode button is working much more consistently now. The only hangup I've seen is that when activating it, it doesn't always immediately redraw the button in blue, instead waiting for the first action following activating it to update the control panel. This doesn't seem to affect behavior though, just a display issue I'm still ironing out.
-Lights placed in symmetry mode now have drivers that track PSR adjustments after the fact, so if you move the original the mirrored light follows. This only works for moving the original light, however. The mirrored light is fully driven by the original and is therefore immovable. Best practice might be to always place lights on the left half of frame so that all of the mirrors are on one side and all of the originals on the other.
-Light preference settings now act as a mirror to the mode selection. If you use a hotkey to switch light types it is reflected in that panel. The panel also works as a mode selector, so if you click on uplights the light placement type switches to uplights too.

New features:

-Point light mode - works the exact same as uplights/downlights, but places a point light at whatever distance from surface you choose. Great for adding a highlight to a particular feature or subject.
-Swivel From Anchor button - with a light selected, you can press this button to automatically change the pivot mode to 3D Cursor and the cursor is automatically moved to the point where the light makes contact with a surface. This will allow you to swivel placed lights from the surface they are lighting after the fact. This cannot work as a permanent change without adding nulls as anchor points which felt like clutter to me, so it is a click-each-time-you-need-it setting currently.


------------------------------

Light Builder is an interactive lighting suite designed to drastically speed up scene illumination. It allows the user to enter a placement mode and click any surface in the viewport and instantly populate it with lights.

There are five modes currently:

Uplight Mode Quickly places lights aligned to surface normals facing upward to simulate floor or ground lighting. You can click to place individual lights or use Shift + Click twice to draw an array of lights along the line between those two points, using the arrow keys to add or subtract the number of lights along the line.

Downlight Mode Functions identically to Uplight mode but aligns the lights to face downward from your target surface, perfect for ceiling lighting. You can instantly toggle back and forth between Uplight and Downlight modes on the fly by pressing TAB.

Targeted Mode Built for precise area lighting, this mode anchors a light to your clicked location and lets you interactively orbit the light around that exact point. Moving your cursor dynamically adjusts the rotation angle until you click or hit enter to commit the angle, allowing you to dynamically adjust the distance from the target in real-time before you click or hit enter to lock it in.

Aimed Mode drops a spotlight either on a clicked surface or automatically at the deepest back boundary of your scene's bounding box. You can then dynamically aim the spot directly at your cursor until you click or hit enter to commit the angle, then adjust its distance along that aiming vector before confirming the placement with another click.

Isolated Rim Light Rig is unavailable in placement mode as it relies on selecting objects you would like to rim light. Once selected, hitting the Isolated Rim Light Rig button will add an area light to your scene that is scaled and positioned according to the bounding box of the selected objects, and is light linked to those object so that they are the only objects receiving light from the newly added rim light.

There's also a symmetry mode that when enabled will make a live copy of the lights you add and manipulate on the opposite side of the X axis, as you add them. This is great for saving time lighting in scenes that are symmetrical.

There are also some parameters that can be adjusted such as creating new stored defaults for the different light types as well as changing the linking behavior when adding new lights.
