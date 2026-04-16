package com.antigravity.speechtotext

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.provider.Settings
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText

class SettingsActivity : AppCompatActivity() {

    companion object {
        private const val REQUEST_RECORD_AUDIO = 1
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        requestMicrophonePermission()

        val serverUrlInput = findViewById<TextInputEditText>(R.id.serverUrlInput)
        val modeGroup = findViewById<RadioGroup>(R.id.modeGroup)
        val saveBtn = findViewById<MaterialButton>(R.id.saveBtn)
        val enableServiceBtn = findViewById<MaterialButton>(R.id.enableServiceBtn)
        val openWebSettingsBtn = findViewById<MaterialButton>(R.id.openWebSettingsBtn)

        // Load saved preferences
        val prefs = getSharedPreferences("stt_prefs", MODE_PRIVATE)
        serverUrlInput.setText(prefs.getString("server_url", ""))

        when (prefs.getString("mode", "raw")) {
            "raw" -> modeGroup.check(R.id.modeRaw)
            "cleanup" -> modeGroup.check(R.id.modeCleanup)
            "rephrase" -> modeGroup.check(R.id.modeRephrase)
        }

        // Save settings
        saveBtn.setOnClickListener {
            val serverUrl = serverUrlInput.text.toString().trim()
            val mode = when (modeGroup.checkedRadioButtonId) {
                R.id.modeCleanup -> "cleanup"
                R.id.modeRephrase -> "rephrase"
                else -> "raw"
            }

            prefs.edit()
                .putString("server_url", serverUrl)
                .putString("mode", mode)
                .apply()

            Toast.makeText(this, "Einstellungen gespeichert", Toast.LENGTH_SHORT).show()
        }

        // Open Accessibility Settings
        enableServiceBtn.setOnClickListener {
            val intent = Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
            startActivity(intent)
        }

        // Open WebView with the full web UI for advanced settings
        openWebSettingsBtn.setOnClickListener {
            val url = serverUrlInput.text.toString().trim()
            if (url.isBlank()) {
                Toast.makeText(this, "Bitte erst Server-URL speichern", Toast.LENGTH_SHORT).show()
            } else {
                startActivity(Intent(this, WebSettingsActivity::class.java))
            }
        }
    }

    private fun requestMicrophonePermission() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                REQUEST_RECORD_AUDIO,
            )
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_RECORD_AUDIO) {
            if (grantResults.isEmpty() || grantResults[0] != PackageManager.PERMISSION_GRANTED) {
                Toast.makeText(
                    this,
                    "Mikrofon-Berechtigung wird fuer die Spracherkennung benoetigt",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }
    }
}
