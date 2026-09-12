import * as THREE from 'three';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { mixamoVRMRigMap } from './mixamoVRMRigMap.js';

/**
 * Load Mixamo animation, convert for three-vrm use, and return it.
 *
 * @param {string} url A url of mixamo animation data
 * @param {VRM} vrm A target VRM
 * @param {Object} options Optional settings
 * @param {boolean} options.stripRootMotion - If true, removes X/Z translation from hips (keeps Y for foot contact)
 * @param {boolean} options.makeAdditive - If true, makes the clip additive (subtracts first frame)
 * @returns {Promise<THREE.AnimationClip>} The converted AnimationClip
 */
export function loadMixamoAnimation( url, vrm, options = {} ) {

	const { stripRootMotion = false, makeAdditive = false, clipName = null } = options;

	const loader = new FBXLoader(); // A loader which loads FBX
	return loader.loadAsync( url ).then( ( asset ) => {

		const clip = THREE.AnimationClip.findByName( asset.animations, 'mixamo.com' ); // extract the AnimationClip

		const tracks = []; // KeyframeTracks compatible with VRM will be added here

		const restRotationInverse = new THREE.Quaternion();
		const parentRestWorldRotation = new THREE.Quaternion();
		const _quatA = new THREE.Quaternion();
		const _vec3 = new THREE.Vector3();

		// Adjust with reference to hips height.
		const motionHipsHeight = asset.getObjectByName( 'mixamorigHips' ).position.y;
		const vrmHipsHeight = vrm.humanoid.normalizedRestPose.hips.position[ 1 ];
		const hipsPositionScale = vrmHipsHeight / motionHipsHeight;

		// Get the hips bone name in VRM for root motion stripping
		const hipsVrmNodeName = vrm.humanoid?.getNormalizedBoneNode( 'hips' )?.name;

		clip.tracks.forEach( ( track ) => {

			// Convert each tracks for VRM use, and push to `tracks`
			const trackSplitted = track.name.split( '.' );
			const mixamoRigName = trackSplitted[ 0 ];
			const vrmBoneName = mixamoVRMRigMap[ mixamoRigName ];
			const vrmNodeName = vrm.humanoid?.getNormalizedBoneNode( vrmBoneName )?.name;
			const mixamoRigNode = asset.getObjectByName( mixamoRigName );

			if ( vrmNodeName != null ) {

				const propertyName = trackSplitted[ 1 ];

				// Store rotations of rest-pose.
				mixamoRigNode.getWorldQuaternion( restRotationInverse ).invert();
				mixamoRigNode.parent.getWorldQuaternion( parentRestWorldRotation );

				if ( track instanceof THREE.QuaternionKeyframeTrack ) {

					// Retarget rotation of mixamoRig to NormalizedBone.
					for ( let i = 0; i < track.values.length; i += 4 ) {

						const flatQuaternion = track.values.slice( i, i + 4 );

						_quatA.fromArray( flatQuaternion );

						_quatA
							.premultiply( parentRestWorldRotation )
							.multiply( restRotationInverse );

						_quatA.toArray( flatQuaternion );

						flatQuaternion.forEach( ( v, index ) => {

							track.values[ index + i ] = v;

						} );

					}

					tracks.push(
						new THREE.QuaternionKeyframeTrack(
							`${vrmNodeName}.${propertyName}`,
							track.times,
							track.values.map( ( v, i ) => ( vrm.meta?.metaVersion === '0' && i % 2 === 0 ? - v : v ) ),
						),
					);

				} else if ( track instanceof THREE.VectorKeyframeTrack ) {

					let value = track.values.map( ( v, i ) => ( vrm.meta?.metaVersion === '0' && i % 3 !== 1 ? - v : v ) * hipsPositionScale );

					// Strip root motion (X/Z translation) from hips if requested
					// This keeps the character in place while the movement controller handles actual translation
					if ( stripRootMotion && vrmBoneName === 'hips' && propertyName === 'position' ) {
						// Get the first frame's X and Z values to use as baseline
						const baseX = value[ 0 ];
						const baseZ = value[ 2 ];

						// Zero out X and Z translation, keep Y for vertical motion (jumping, bobbing)
						value = value.map( ( v, i ) => {
							const component = i % 3;
							if ( component === 0 ) return 0; // X - zero out horizontal
							if ( component === 2 ) return 0; // Z - zero out horizontal
							return v; // Y - keep vertical motion
						} );

						console.log( `🦶 Stripped root motion from hips position track` );
					}

					tracks.push( new THREE.VectorKeyframeTrack( `${vrmNodeName}.${propertyName}`, track.times, value ) );

				}

			}

		} );

		// Use custom name or generate from URL
		const animationName = clipName || url.split('/').pop().replace('.fbx', '').replace('.FBX', '');
		let resultClip = new THREE.AnimationClip( animationName, clip.duration, tracks );

		// Make the clip additive if requested (for layered animations)
		if ( makeAdditive ) {
			THREE.AnimationUtils.makeClipAdditive( resultClip );
			console.log( `➕ Made clip additive: ${animationName}` );
		}

		return resultClip;

	} );

}
