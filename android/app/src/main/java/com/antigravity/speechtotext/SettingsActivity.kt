package com.antigravity.speechtotext

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.RadioGroup
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val serverUrlInput = findViewById<TextInputEditText>(R.id.serverUrlInput)
        val modeGroup = findViewById<RadioGroup>(R.id.modeGroup)
        val saveBtn = findViewById<MaterialButton>(R.id.saveBtn)
        val enableServiceBtn = findViewById<MaterialButton>(R.id.enableServiceBtn)

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
    }
}
